import os
import re
import json
import pathlib
import asyncio
from datetime import datetime
from typing import Dict, Any, List, Optional, Callable

import httpx
from fastapi.concurrency import run_in_threadpool

from .notion_client import build_client, notion_retry
from .config import Settings
from .utils_id import normalize_notion_id

ASSET_TYPES = {"image", "file", "pdf", "video", "audio"}
ASSET_CONCURRENCY = 5
ASSET_CHUNK = 128 * 1024

def safe_slug(text: str, default: str = "page") -> str:
    text = (text or "").strip()
    text = re.sub(r"[^\w\-]+", "_", text)[:60] or default
    return text

async def ensure_dir(path: str):
    pathlib.Path(path).mkdir(parents=True, exist_ok=True)

async def download_asset(url: str, dest_path: str, timeout: int):
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        async with client.stream("GET", url) as r:
            r.raise_for_status()
            pathlib.Path(os.path.dirname(dest_path)).mkdir(parents=True, exist_ok=True)
            with open(dest_path, "wb") as f:
                async for chunk in r.aiter_bytes(ASSET_CHUNK):
                    if chunk:
                        f.write(chunk)

def _page_title_from_properties(props: Dict[str, Any]) -> str:
    for _, v in props.items():
        if v.get("type") == "title":
            s = "".join(t.get("plain_text", "") for t in v.get("title", []))
            return s or "untitled"
    return "untitled"

class NotionDumpService:
    """
    children.list 1패스로 스냅샷 + 매니페스트 구축.
    manifest.nodes[*].files[] = {url, path, original, saved}
    """
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = build_client(settings.NOTION_TOKEN, settings.NOTION_TIMEOUT)

    @notion_retry()
    def _get_page(self, page_id: str) -> Dict[str, Any]:
        return self.client.pages.retrieve(page_id=page_id)

    @notion_retry()
    def _list_children(self, block_id: str, start_cursor: Optional[str] = None) -> Dict[str, Any]:
        return self.client.blocks.children.list(block_id=block_id, start_cursor=start_cursor, page_size=100)

    async def dump_page_tree(
        self,
        root_page_id: str,
        progress_cb: Optional[Callable[[int, str], None]] = None,
        cancel_cb: Optional[Callable[[], bool]] = None,
    ) -> str:
        def check_cancel():
            if cancel_cb and cancel_cb():
                raise asyncio.CancelledError()

        if progress_cb: progress_cb(1, "Normalizing page ID")
        root_page_id = normalize_notion_id(root_page_id)

        if progress_cb: progress_cb(3, "Fetching root page")
        page = await run_in_threadpool(self._get_page, root_page_id)
        title = _page_title_from_properties(page.get("properties", {}))

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dump_name = f"{safe_slug(title)}_{stamp}"
        root_dir = os.path.join(self.settings.DUMP_ROOT, dump_name)
        await ensure_dir(root_dir)
        if progress_cb: progress_cb(5, f"Preparing folder: {dump_name}")

        manifest = {"root_page_id": root_page_id, "title": title, "created_at": stamp,
                    "static_base_url": self.settings.STATIC_BASE_URL, "nodes": []}

        async def walk_children(parent_id: str, rel_dir: str) -> List[Dict[str, Any]]:
            snapshot_children: List[Dict[str, Any]] = []
            cursor: Optional[str] = None
            downloads: List[asyncio.Task] = []

            while True:
                check_cancel()
                res = await run_in_threadpool(self._list_children, parent_id, cursor)
                for b in res.get("results", []):
                    t = b.get("type")
                    snap = {"id": b.get("id"), "type": t, "has_children": b.get("has_children", False),
                            t: b.get(t, {}) or {}, "children": []}
                    man = {"id": b.get("id"), "type": t, "has_children": b.get("has_children", False), "files": []}

                    data = b.get(t, {}) or {}
                    if t in ASSET_TYPES:
                        fobj = data.get("file") or data.get("external")
                        if fobj and fobj.get("url"):
                            url = fobj["url"]
                            pure = url.split("?")[0]
                            original = os.path.basename(pure) or "file.bin"
                            ext = os.path.splitext(pure)[1] or ".bin"
                            saved = f"{b['id']}{ext}"
                            out_path = os.path.join(self.settings.DUMP_ROOT, rel_dir, saved)
                            await ensure_dir(os.path.dirname(out_path))
                            downloads.append(asyncio.create_task(download_asset(url, out_path, self.settings.NOTION_TIMEOUT)))
                            rel = os.path.relpath(out_path, self.settings.DUMP_ROOT).replace("\\", "/")
                            man["files"].append({"url": url, "path": rel, "original": original, "saved": saved})

                    if b.get("has_children"):
                        snap["children"] = await walk_children(b["id"], rel_dir)

                    snapshot_children.append(snap)
                    manifest["nodes"].append(man)

                if not res.get("has_more"): break
                cursor = res.get("next_cursor")

            if downloads:
                if progress_cb: progress_cb(90, f"Downloading {len(downloads)} assets")
                await asyncio.gather(*downloads)
            return snapshot_children

        snapshot_root = {"id": root_page_id, "type": "root", "has_children": True,
                         "children": await walk_children(root_page_id, os.path.basename(root_dir))}

        with open(os.path.join(root_dir, "tree.json"), "w", encoding="utf-8") as f:
            json.dump(snapshot_root, f, ensure_ascii=False, indent=2)
        with open(os.path.join(root_dir, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

        if progress_cb: progress_cb(100, "Complete")
        return root_dir