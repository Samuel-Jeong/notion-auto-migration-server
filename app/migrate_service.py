import os
import mimetypes
import logging
import asyncio
from typing import Dict, Any, List, Optional, Callable

import httpx
from fastapi.concurrency import run_in_threadpool

from .notion_client import build_client, notion_retry
from .config import Settings
from .utils_id import normalize_notion_id

APPEND_LIMIT = 100
ASSET_BLOCK_TYPES = ("image", "file", "pdf", "video", "audio", "external")
NOTION_VERSION = "2022-06-28"   # File upload endpoint supported version

# Upload allowed capacity (default 20MB)
DEFAULT_UPLOAD_MB = 20

logger = logging.getLogger("app.migrate_service")


class NotionMigrateService:
    """
    - Recursively create under target_page_id based on tree.json
    - Files/images are uploaded to Notion storage from local dump files and attached
    - Progress callback (progress_cb) support
    """
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = build_client(settings.NOTION_TOKEN, settings.NOTION_TIMEOUT)

        self.upload_max_bytes = int(os.environ.get("ASSET_UPLOAD_MAX_MB", DEFAULT_UPLOAD_MB)) * 1024 * 1024
        self._upload_cache: Dict[str, str] = {}  # local_path -> upload_id
        self._upload_lock = asyncio.Lock()

    @notion_retry()
    def _append_children(self, parent_block_id: str, children: List[Dict[str, Any]]) -> Dict[str, Any]:
        return self.client.blocks.children.append(block_id=parent_block_id, children=children)

    @notion_retry()
    def _create_child_page(self, parent_page_id: str, title: str, children: List[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Create a child page under the specified parent page"""
        page_data = {
            "parent": {"page_id": parent_page_id},
            "properties": {
                "title": {
                    "title": [{"type": "text", "text": {"content": title}}]
                }
            }
        }
        
        # Add children blocks if provided
        if children:
            page_data["children"] = children
        
        return self.client.pages.create(**page_data)

    # -------------------------------
    # Notion File Uploads API
    # -------------------------------
    async def _create_file_upload(self, file_name: str, content_type: str) -> Optional[Dict[str, Any]]:
        headers = {
            "Authorization": f"Bearer {self.settings.NOTION_TOKEN}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }
        payload = {"file_name": file_name, "content_type": content_type}
        try:
            async with httpx.AsyncClient(timeout=self.settings.NOTION_TIMEOUT) as client:
                r = await client.post("https://api.notion.com/v1/file_uploads", headers=headers, json=payload)
                r.raise_for_status()
                return r.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error creating file upload for {file_name}: {e.response.status_code} - {e.response.text}")
            return None
        except httpx.TimeoutException:
            logger.error(f"Timeout creating file upload for {file_name}")
            return None
        except Exception as e:
            logger.exception(f"Unexpected error creating file upload for {file_name}: {e}")
            return None

    async def _send_file_upload(self, upload_id: str, local_path: str, content_type: str) -> bool:
        """Upload file directly to Notion using the /file_uploads/{id}/send endpoint"""
        file_name = os.path.basename(local_path)
        upload_url = f"https://api.notion.com/v1/file_uploads/{upload_id}/send"
        
        headers = {
            "Authorization": f"Bearer {self.settings.NOTION_TOKEN}",
            "Notion-Version": NOTION_VERSION,
        }
        
        try:
            async with httpx.AsyncClient(timeout=max(10, self.settings.NOTION_TIMEOUT)) as client:
                with open(local_path, "rb") as fp:
                    files = {"file": (file_name, fp, content_type)}
                    r = await client.post(upload_url, files=files, headers=headers)
                    if r.status_code // 100 == 2:
                        logger.info(f"Successfully uploaded {file_name} to Notion")
                        return True
                    logger.error(f"File upload failed for {file_name}: {r.status_code} - {r.text}")
                    return False
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error uploading {file_name}: {e.response.status_code} - {e.response.text}")
            return False
        except httpx.TimeoutException:
            logger.error(f"Timeout uploading {file_name}")
            return False
        except Exception as e:
            logger.exception(f"Unexpected error uploading {file_name}: {e}")
            return False

    async def _upload_to_notion(self, local_path: str) -> Optional[str]:
        async with self._upload_lock:
            if local_path in self._upload_cache:
                return self._upload_cache[local_path]

        try:
            size = os.path.getsize(local_path)
        except OSError:
            logger.error(f"Failed to get file size for {local_path}")
            return None
        if size > self.upload_max_bytes:
            logger.warning(f"File {local_path} size {size} bytes exceeds limit {self.upload_max_bytes}")
            return None

        ctype = mimetypes.guess_type(local_path)[0] or "application/octet-stream"
        create = await self._create_file_upload(os.path.basename(local_path), ctype)
        if not create:
            logger.error(f"Failed to create file upload object for {local_path}")
            return None

        upload_id = create.get("id")
        if not upload_id:
            logger.error(f"Invalid upload response for {local_path}: missing upload ID")
            return None

        # Upload file directly to Notion using the new API flow
        if not await self._send_file_upload(upload_id, local_path, ctype):
            logger.error(f"Failed to upload file {local_path}")
            return None

        # File is ready to use immediately after upload - no completion step needed
        async with self._upload_lock:
            self._upload_cache[local_path] = upload_id
        logger.info(f"Successfully uploaded and cached {os.path.basename(local_path)} with ID {upload_id}")
        return upload_id

    # -------------------------------
    # Payload conversion
    # -------------------------------
    async def _node_to_block_payload(
        self,
        src_node: Dict[str, Any],
        asset_map: Dict[str, List[Dict[str, Any]]],  # node_id -> [{"local_path","original","rel_path"}...]
    ) -> Dict[str, Any]:
        t = src_node["type"]
        payload = {"object": "block", "type": t, t: src_node.get(t, {}) or {}}

        if t in ASSET_BLOCK_TYPES:
            sub = payload.get(t, {}) or {}
            caption = sub.get("caption") if isinstance(sub, dict) else None

            info = (asset_map.get(src_node.get("id") or "", []) or [{}])[0]
            local_path = info.get("local_path")
            original = info.get("original")

            new_sub: Dict[str, Any]
            upload_id = await self._upload_to_notion(local_path) if local_path else None
            if upload_id:
                new_sub = {"type": "file_upload", "file_upload": {"id": upload_id}}
            else:
                # Upload failed → preserve caption only (don't use external URLs)
                new_sub = {}

            if not caption and original:
                caption = [{"type": "text", "text": {"content": original}}]
            if caption:
                new_sub["caption"] = caption

            payload[t] = new_sub or {}  # Empty dict is allowed for append (though file blocks without content are meaningless)

        return payload

    # -------------------------------
    # Recursive creation
    # -------------------------------
    async def _append_children_recursive(
        self,
        parent_id: str,
        src_children: List[Dict[str, Any]],
        asset_map: Dict[str, List[Dict[str, Any]]],
        progress_cb: Optional[Callable[[int, str], None]],
        counter: Dict[str, int],
        total: int,
        check_cancel: Callable[[], None],
    ):
        if not src_children:
            return

        # Process all blocks in their original order to preserve positioning
        regular_block_batch = []
        
        for node in src_children:
            check_cancel()
            
            if node.get("type") == "child_page":
                # Before processing a child_page, first append any batched regular blocks
                if regular_block_batch:
                    await self._process_regular_blocks_batch(
                        parent_id, regular_block_batch, asset_map, progress_cb, counter, total, check_cancel
                    )
                    regular_block_batch = []
                
                # Process child_page block immediately to maintain order
                await self._process_child_page_block(
                    parent_id, node, asset_map, progress_cb, counter, total, check_cancel
                )
            else:
                # Accumulate regular blocks for batch processing
                regular_block_batch.append(node)
        
        # Process any remaining regular blocks at the end
        if regular_block_batch:
            await self._process_regular_blocks_batch(
                parent_id, regular_block_batch, asset_map, progress_cb, counter, total, check_cancel
            )

    async def _process_regular_blocks_batch(
        self,
        parent_id: str,
        regular_blocks: List[Dict[str, Any]],
        asset_map: Dict[str, List[Dict[str, Any]]],
        progress_cb: Optional[Callable[[int, str], None]],
        counter: Dict[str, int],
        total: int,
        check_cancel: Callable[[], None],
    ):
        """Process a batch of regular blocks in chunks"""
        for i in range(0, len(regular_blocks), APPEND_LIMIT):
            check_cancel()
            
            chunk = regular_blocks[i:i + APPEND_LIMIT]
            
            payload_chunk: List[Dict[str, Any]] = []
            for n in chunk:
                payload_chunk.append(await self._node_to_block_payload(n, asset_map))

            try:
                resp = await run_in_threadpool(self._append_children, parent_id, payload_chunk)
                results: List[Dict[str, Any]] = resp.get("results", [])
            except Exception:
                results = []
                for n in chunk:
                    check_cancel()
                    try:
                        single = await self._node_to_block_payload(n, asset_map)
                        r = await run_in_threadpool(self._append_children, parent_id, [single])
                        results.append((r.get("results") or [{}])[0])
                    except Exception:
                        results.append({})

            for idx, src_node in enumerate(chunk):
                counter["done"] += 1
                if progress_cb:
                    pct = int(min(99, (counter["done"] / max(1, total)) * 100))
                    progress_cb(pct, f"Creating {counter['done']}/{total}")

                created = results[idx] if idx < len(results) else {}
                created_id = created.get("id")
                if src_node.get("has_children") and src_node.get("children"):
                    await self._append_children_recursive(
                        created_id or parent_id, src_node["children"], asset_map, progress_cb, counter, total, check_cancel
                    )

    async def _process_child_page_block(
        self,
        parent_id: str,
        child_page_node: Dict[str, Any],
        asset_map: Dict[str, List[Dict[str, Any]]],
        progress_cb: Optional[Callable[[int, str], None]],
        counter: Dict[str, int],
        total: int,
        check_cancel: Callable[[], None],
    ):
        """Process a single child_page block"""
        check_cancel()
        
        # Extract page title from child_page data
        child_page_data = child_page_node.get("child_page", {})
        page_title = child_page_data.get("title", "Untitled Page")
        
        try:
            # Create the child page
            page_resp = await run_in_threadpool(self._create_child_page, parent_id, page_title)
            created_page_id = page_resp.get("id")
            
            counter["done"] += 1
            if progress_cb:
                pct = int(min(99, (counter["done"] / max(1, total)) * 100))
                progress_cb(pct, f"Creating page '{page_title}' {counter['done']}/{total}")
            
            logger.info(f"Successfully created child page '{page_title}' with ID {created_page_id} in original position")
            
            # Recursively migrate the page's children to the newly created page
            if child_page_node.get("has_children") and child_page_node.get("children") and created_page_id:
                await self._append_children_recursive(
                    created_page_id, child_page_node["children"], asset_map, progress_cb, counter, total, check_cancel
                )
                
        except Exception as e:
            logger.error(f"Failed to create child page '{page_title}': {e}")
            counter["done"] += 1

    async def migrate_under(
        self,
        target_page_id: str,
        tree: Dict[str, Any],
        asset_map: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        progress_cb: Optional[Callable[[int, str], None]] = None,
        cancel_cb: Optional[Callable[[], bool]] = None,
    ):
        def check_cancel():
            if cancel_cb and cancel_cb():
                raise asyncio.CancelledError()

        if progress_cb: progress_cb(1, "Normalizing target page ID")
        target_page_id = normalize_notion_id(target_page_id)

        asset_map = asset_map or {}
        children = tree.get("children", [])

        def count_nodes(n: Dict[str, Any]) -> int:
            return 1 + sum(count_nodes(c) for c in n.get("children", []))
        total = max(1, sum(count_nodes(c) for c in children))
        counter = {"done": 0}
        if progress_cb: progress_cb(3, "Starting children creation (upload mode)")

        await self._append_children_recursive(target_page_id, children, asset_map, progress_cb, counter, total, check_cancel)
        if progress_cb: progress_cb(100, "Complete")