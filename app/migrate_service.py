import os
import mimetypes
import logging
import asyncio
from typing import Dict, Any, List, Optional, Callable

import httpx
from fastapi.concurrency import run_in_threadpool

from .notion_client import build_client, notion_retry, create_database, get_page, get_database
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

    async def _check_target_type(self, target_id: str) -> str:
        """Check if target is a page or database. Returns 'page' or 'database'."""
        try:
            # First try to get it as a page
            await run_in_threadpool(get_page, self.client, target_id)
            return "page"
        except Exception:
            try:
                # If that fails, try to get it as a database
                await run_in_threadpool(get_database, self.client, target_id)
                return "database"
            except Exception:
                raise ValueError(f"Target ID {target_id} is neither a valid page nor database")

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

    async def migrate_database_under(
        self,
        target_page_id: str,
        database_tree: Dict[str, Any],
        asset_map: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        progress_cb: Optional[Callable[[int, str], None]] = None,
        cancel_cb: Optional[Callable[[], bool]] = None,
    ) -> str:
        """Migrate a database tree under a target page"""
        def check_cancel():
            if cancel_cb and cancel_cb():
                raise asyncio.CancelledError()

        if progress_cb: progress_cb(1, "Normalizing target page ID")
        target_page_id = normalize_notion_id(target_page_id)

        # Check if target is a valid page (databases cannot be parented by databases)
        if progress_cb: progress_cb(2, "Validating target type")
        try:
            target_type = await self._check_target_type(target_page_id)
            if target_type == "database":
                error_msg = "Cannot create databases parented by a database. Target must be a page."
                logger.error(f"Database migration failed: {error_msg}")
                if progress_cb: progress_cb(100, f"Failed: {error_msg}")
                raise ValueError(error_msg)
            logger.info(f"Target {target_page_id} is a valid page for database creation")
        except ValueError as e:
            # Re-raise ValueError with our custom message
            raise e
        except Exception as e:
            error_msg = f"Failed to validate target type: {e}"
            logger.error(f"Database migration failed: {error_msg}")
            if progress_cb: progress_cb(100, f"Failed: {error_msg}")
            raise ValueError(error_msg)

        # Extract database information
        db_title = database_tree.get("title", "Migrated Database")
        db_properties = database_tree.get("properties", {})
        db_entries = database_tree.get("entries", [])
        
        asset_map = asset_map or {}

        if progress_cb: progress_cb(5, f"Creating database: {db_title}")
        check_cancel()

        # Convert dump properties to creation format
        creation_properties = self._convert_dump_schema_to_creation_format(db_properties)

        # Create the database
        try:
            new_db = await run_in_threadpool(create_database, self.client, target_page_id, db_title, creation_properties)
            new_db_id = new_db.get("id")
            if not new_db_id:
                raise ValueError("Failed to get new database ID")
            
            logger.info(f"Successfully created database '{db_title}' with ID {new_db_id}")
        except Exception as e:
            logger.error(f"Failed to create database '{db_title}': {e}")
            if progress_cb: progress_cb(100, f"Failed: {e}")
            raise

        # Build option ID mappings for select/multi_select properties
        if progress_cb: progress_cb(7, "Building option ID mappings")
        option_mappings = self._build_option_id_mappings(db_properties, new_db.get("properties", {}))

        if progress_cb: progress_cb(10, f"Migrating {len(db_entries)} database entries")
        
        # Migrate each database entry
        for i, entry in enumerate(db_entries):
            check_cancel()
            progress = 10 + (i * 80) // max(len(db_entries), 1)
            if progress_cb: progress_cb(progress, f"Migrating entry {i + 1}/{len(db_entries)}")
            
            try:
                await self._migrate_database_entry(new_db_id, entry, asset_map, option_mappings, progress_cb, cancel_cb)
            except Exception as e:
                logger.error(f"Failed to migrate database entry {i + 1}: {e}")
                # Continue with other entries even if one fails
                continue

        if progress_cb: progress_cb(100, "Database migration complete")
        return new_db_id

    def _convert_dump_schema_to_creation_format(self, dump_schema: Dict[str, Any]) -> Dict[str, Any]:
        """Convert dump database schema to Notion API creation format"""
        creation_properties = {}
        
        for prop_name, prop_data in dump_schema.items():
            if not isinstance(prop_data, dict):
                continue
                
            prop_type = prop_data.get("type")
            if not prop_type:
                continue
            
            # Create property definition for database creation
            # Remove metadata like id, name, description and keep only the type-specific configuration
            creation_prop = {"type": prop_type}
            
            if prop_type == "select" and "select" in prop_data:
                creation_prop["select"] = prop_data["select"]
            elif prop_type == "multi_select" and "multi_select" in prop_data:
                creation_prop["multi_select"] = prop_data["multi_select"]
            elif prop_type == "rich_text":
                creation_prop["rich_text"] = {}
            elif prop_type == "title":
                creation_prop["title"] = {}
            elif prop_type == "number" and "number" in prop_data:
                creation_prop["number"] = prop_data["number"]
            elif prop_type == "checkbox":
                creation_prop["checkbox"] = {}
            elif prop_type == "url":
                creation_prop["url"] = {}
            elif prop_type == "email":
                creation_prop["email"] = {}
            elif prop_type == "phone_number":
                creation_prop["phone_number"] = {}
            elif prop_type == "date" and "date" in prop_data:
                creation_prop["date"] = prop_data["date"]
            elif prop_type == "people":
                creation_prop["people"] = {}
            elif prop_type == "files":
                creation_prop["files"] = {}
            elif prop_type == "relation" and "relation" in prop_data:
                creation_prop["relation"] = prop_data["relation"]
            elif prop_type == "status" and "status" in prop_data:
                creation_prop["status"] = prop_data["status"]
            elif prop_type == "formula" and "formula" in prop_data:
                creation_prop["formula"] = prop_data["formula"]
            elif prop_type == "rollup" and "rollup" in prop_data:
                creation_prop["rollup"] = prop_data["rollup"]
            elif prop_type in ("created_time", "created_by", "last_edited_time", "last_edited_by"):
                # These are system-generated properties, but we still need to define them for database creation
                creation_prop[prop_type] = {}
            else:
                logger.debug(f"Unsupported property type '{prop_type}' for property '{prop_name}' in database creation")
                continue
            
            creation_properties[prop_name] = creation_prop
            logger.debug(f"Converted schema property '{prop_name}': {creation_prop}")
        
        return creation_properties

    def _build_option_id_mappings(self, original_properties: Dict[str, Any], new_properties: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
        """Build mappings from original option IDs to new option IDs for select/multi_select properties"""
        mappings = {}
        
        for prop_name, original_prop in original_properties.items():
            if not isinstance(original_prop, dict):
                continue
                
            prop_type = original_prop.get("type")
            if prop_type not in ("select", "multi_select"):
                continue
                
            # Get the corresponding new property
            new_prop = new_properties.get(prop_name)
            if not new_prop or not isinstance(new_prop, dict):
                logger.warning(f"New property '{prop_name}' not found or invalid")
                continue
                
            # Extract options from both original and new properties
            original_options = original_prop.get(prop_type, {}).get("options", [])
            new_options = new_prop.get(prop_type, {}).get("options", [])
            
            if not original_options or not new_options:
                logger.warning(f"No options found for property '{prop_name}'")
                continue
            
            # Build mapping by matching option names
            option_mapping = {}
            for orig_opt in original_options:
                orig_id = orig_opt.get("id")
                orig_name = orig_opt.get("name")
                
                if not orig_id or not orig_name:
                    continue
                    
                # Find matching option in new database by name
                for new_opt in new_options:
                    new_id = new_opt.get("id")
                    new_name = new_opt.get("name")
                    
                    if new_name == orig_name and new_id:
                        option_mapping[orig_id] = new_id
                        logger.debug(f"Property '{prop_name}': Mapped option '{orig_name}' {orig_id} -> {new_id}")
                        break
                else:
                    logger.warning(f"No matching option found for '{orig_name}' in property '{prop_name}'")
            
            if option_mapping:
                mappings[prop_name] = option_mapping
                logger.info(f"Built option mapping for property '{prop_name}': {len(option_mapping)} options mapped")
        
        return mappings

    def _convert_dump_properties_to_notion_format(self, dump_properties: Dict[str, Any], option_mappings: Optional[Dict[str, Dict[str, str]]] = None) -> Dict[str, Any]:
        """Convert dump property format to Notion API format for creating database entries"""
        notion_properties = {}
        
        # Read-only system properties that cannot be set when creating database entries
        READONLY_PROPERTIES = {
            "created_time", "created_by", "last_edited_time", "last_edited_by", 
            "rollup", "formula"
        }
        
        for prop_name, prop_data in dump_properties.items():
            if not isinstance(prop_data, dict):
                continue
                
            prop_type = prop_data.get("type")
            if not prop_type:
                continue
            
            # Skip read-only system properties that cannot be set during creation
            if prop_type in READONLY_PROPERTIES:
                logger.debug(f"Skipping read-only property '{prop_name}' of type '{prop_type}'")
                continue
            
            # Extract only the actual property value, removing all metadata
            notion_prop = {}
            
            # Map each writable property type to extract the correct value
            if prop_type == "select" and "select" in prop_data:
                # Only include select if it's not null
                if prop_data["select"] is not None:
                    select_value = prop_data["select"]
                    # Apply option ID mapping if available
                    if option_mappings and prop_name in option_mappings and isinstance(select_value, dict):
                        original_id = select_value.get("id")
                        if original_id and original_id in option_mappings[prop_name]:
                            new_id = option_mappings[prop_name][original_id]
                            select_value = dict(select_value)  # Copy to avoid modifying original
                            select_value["id"] = new_id
                            logger.debug(f"Mapped select option for '{prop_name}': {original_id} -> {new_id}")
                    notion_prop = {"select": select_value}
            elif prop_type == "multi_select" and "multi_select" in prop_data:
                multi_select_value = prop_data["multi_select"]
                # Apply option ID mapping if available
                if option_mappings and prop_name in option_mappings and isinstance(multi_select_value, list):
                    mapped_options = []
                    for option in multi_select_value:
                        if isinstance(option, dict):
                            original_id = option.get("id")
                            if original_id and original_id in option_mappings[prop_name]:
                                new_id = option_mappings[prop_name][original_id]
                                mapped_option = dict(option)  # Copy to avoid modifying original
                                mapped_option["id"] = new_id
                                mapped_options.append(mapped_option)
                                logger.debug(f"Mapped multi_select option for '{prop_name}': {original_id} -> {new_id}")
                            else:
                                mapped_options.append(option)
                        else:
                            mapped_options.append(option)
                    multi_select_value = mapped_options
                notion_prop = {"multi_select": multi_select_value}
            elif prop_type == "rich_text" and "rich_text" in prop_data:
                notion_prop = {"rich_text": prop_data["rich_text"]}
            elif prop_type == "title" and "title" in prop_data:
                notion_prop = {"title": prop_data["title"]}
            elif prop_type == "number" and "number" in prop_data:
                # Only include number if it's not null
                if prop_data["number"] is not None:
                    notion_prop = {"number": prop_data["number"]}
            elif prop_type == "checkbox" and "checkbox" in prop_data:
                notion_prop = {"checkbox": prop_data["checkbox"]}
            elif prop_type == "url" and "url" in prop_data:
                # Only include URL if it's not null
                if prop_data["url"] is not None:
                    notion_prop = {"url": prop_data["url"]}
            elif prop_type == "email" and "email" in prop_data:
                # Only include email if it's not null
                if prop_data["email"] is not None:
                    notion_prop = {"email": prop_data["email"]}
            elif prop_type == "phone_number" and "phone_number" in prop_data:
                # Only include phone_number if it's not null
                if prop_data["phone_number"] is not None:
                    notion_prop = {"phone_number": prop_data["phone_number"]}
            elif prop_type == "date" and "date" in prop_data:
                # Only include date if it's not null
                if prop_data["date"] is not None:
                    notion_prop = {"date": prop_data["date"]}
            elif prop_type == "people" and "people" in prop_data:
                notion_prop = {"people": prop_data["people"]}
            elif prop_type == "files" and "files" in prop_data:
                notion_prop = {"files": prop_data["files"]}
            elif prop_type == "relation" and "relation" in prop_data:
                notion_prop = {"relation": prop_data["relation"]}
            elif prop_type == "status" and "status" in prop_data:
                # Only include status if it's not null
                if prop_data["status"] is not None:
                    notion_prop = {"status": prop_data["status"]}
            else:
                logger.debug(f"Unsupported property type '{prop_type}' for property '{prop_name}'")
                continue
            
            # Only add the property if we successfully extracted a value
            if notion_prop:
                notion_properties[prop_name] = notion_prop
                logger.debug(f"Converted property '{prop_name}': {notion_prop}")
        
        return notion_properties

    async def _migrate_database_entry(self, database_id: str, entry: Dict[str, Any], asset_map: Dict[str, List[Dict[str, Any]]], 
                                     option_mappings: Optional[Dict[str, Dict[str, str]]] = None,
                                     progress_cb: Optional[Callable[[int, str], None]] = None, 
                                     cancel_cb: Optional[Callable[[], bool]] = None):
        """Migrate a single database entry using the same robust processing as page migration"""
        def check_cancel():
            if cancel_cb and cancel_cb():
                raise asyncio.CancelledError()
        
        dump_properties = entry.get("properties", {})
        entry_content = entry.get("content", [])
        
        # Convert dump properties to Notion API format with option mappings
        notion_properties = self._convert_dump_properties_to_notion_format(dump_properties, option_mappings)
        
        # Create a new page in the database (without initial children to avoid Notion limits)
        try:
            page_data = {
                "parent": {"database_id": database_id},
                "properties": notion_properties
            }

            # Create the page first without children
            new_page = await run_in_threadpool(self.client.pages.create, **page_data)
            new_page_id = new_page.get("id")
            
            if not new_page_id:
                raise ValueError("Failed to get new page ID from database entry creation")
            
            # Now use the robust recursive processing for all content
            if entry_content:
                # Count total nodes for progress tracking
                def count_nodes(n: Dict[str, Any]) -> int:
                    return 1 + sum(count_nodes(c) for c in n.get("children", []))
                total = max(1, sum(count_nodes(c) for c in entry_content))
                counter = {"done": 0}
                
                # Use the same robust recursive processing as page migration
                await self._append_children_recursive(
                    new_page_id, entry_content, asset_map, progress_cb, counter, total, check_cancel
                )
                        
        except Exception as e:
            logger.error(f"Failed to create database entry page: {e}")
            raise
