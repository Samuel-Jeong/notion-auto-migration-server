import os, re, json, pathlib, asyncio
from datetime import datetime
from typing import Dict, Any, List, Optional
import httpx
from fastapi.concurrency import run_in_threadpool
from .notion_client import build_client, notion_retry
from .config import Settings
from .utils_id import normalize_notion_id

ASSET_TYPES = {"image", "file", "pdf", "video", "audio"}

def safe_slug(text: str, default: str = "page") -> str:
    text = (text or "").strip()
    text = re.sub(r"[^\w\-]+", "_", text)[:60] or default
    return text

async def ensure_dir(path: str):
    pathlib.Path(path).mkdir(parents=True, exist_ok=True)

async def download_asset(url: str, dest_path: str, timeout: int = 30):
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(url)
        r.raise_for_status()
        pathlib.Path(os.path.dirname(dest_path)).mkdir(parents=True, exist_ok=True)
        with open(dest_path, "wb") as f:
            f.write(r.content)

def _page_title_from_properties(properties: Dict[str, Any]) -> str:
    for k, v in properties.items():
        if v.get("type") == "title":
            spans = v.get("title", [])
            s = "".join([i.get("plain_text", "") for i in spans])
            return s or "untitled"
    return "untitled"

class NotionDumpService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = build_client(settings.NOTION_TOKEN, settings.NOTION_TIMEOUT)

    @notion_retry()
    def _get_page(self, page_id: str) -> Dict[str, Any]:
        return self.client.pages.retrieve(page_id=page_id)

    @notion_retry()
    def _get_block(self, block_id: str) -> Dict[str, Any]:
        return self.client.blocks.retrieve(block_id=block_id)

    @notion_retry()
    def _list_children(self, block_id: str, start_cursor: Optional[str] = None) -> Dict[str, Any]:
        return self.client.blocks.children.list(block_id=block_id, start_cursor=start_cursor, page_size=100)

    async def dump_page_tree(self, root_page_id: str) -> str:
        """root_page_id 이하 전체 트리를 로컬에 덤프하고 루트 폴더 경로를 반환"""
        root_page_id = normalize_notion_id(root_page_id)
        page = await run_in_threadpool(self._get_page, root_page_id)
        title = _page_title_from_properties(page.get("properties", {}))
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        root_dir = os.path.join(self.settings.DUMP_ROOT, f"{safe_slug(title)}_{stamp}")
        await ensure_dir(root_dir)

        # 전체 트리 DFS
        manifest = {
            "root_page_id": root_page_id,
            "title": title,
            "created_at": stamp,
            "static_base_url": self.settings.STATIC_BASE_URL,
            "nodes": []  # 각 노드(page/block) 메타
        }

        async def walk(block_id: str, rel_dir: str):
            # 블록/페이지 메타
            meta = await run_in_threadpool(self._get_block, block_id)
            node = {"id": meta["id"], "type": meta["type"], "has_children": meta.get("has_children", False), "files": []}
            # 페이지면 제목 갱신
            if meta["type"] == "child_page":
                node["title"] = meta.get("child_page", {}).get("title")
            if meta["type"] == "child_database":
                node["title"] = meta.get("child_database", {}).get("title")

            # 블록이 에셋을 가진 경우 다운로드
            t = meta["type"]
            data = meta.get(t, {})
            if t in ("image", "file", "pdf", "video", "audio"):
                fobj = data.get("file") or data.get("external")
                if fobj:
                    url = fobj.get("url")
                    if url:
                        fname = f"{meta['id']}"
                        ext = os.path.splitext(url.split("?")[0])[1] or ".bin"
                        out_path = os.path.join(self.settings.DUMP_ROOT, rel_dir, f"{fname}{ext}")
                        await download_asset(url, out_path, timeout=self.settings.NOTION_TIMEOUT)
                        node["files"].append({
                            "url": url,
                            "path": os.path.relpath(out_path, self.settings.DUMP_ROOT).replace("\\", "/")
                        })

            # children 순회
            children: List[Dict[str, Any]] = []
            if meta.get("has_children"):
                cursor = None
                while True:
                    res = await run_in_threadpool(self._list_children, block_id, cursor)
                    for c in res.get("results", []):
                        children.append(c["id"])
                    if not res.get("has_more"):
                        break
                    cursor = res.get("next_cursor")

            manifest["nodes"].append(node)

            # 자식 디렉터리명
            for cid in children:
                await walk(cid, rel_dir)

        # 루트는 block API로 시작
        await walk(root_page_id, os.path.basename(root_dir))

        # 전체 블록 JSON 스냅샷도 저장 (복원용)
        # 루트 페이지 전체 children 트리를 구조화하여 저장
        tree_path = os.path.join(root_dir, "tree.json")
        page_snapshot = await self._snapshot_tree(root_page_id)
        with open(tree_path, "w", encoding="utf-8") as f:
            json.dump(page_snapshot, f, ensure_ascii=False, indent=2)

        # 매니페스트 저장
        with open(os.path.join(root_dir, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

        return root_dir

    async def _snapshot_tree(self, block_id: str) -> Dict[str, Any]:
        """블록(페이지) 이하 children 전체를 JSON 트리로 직렬화(이미지/파일 URL 포함).
        마이그레이션 시 이 구조를 이용해 블록을 재생성한다.
        """
        meta = await run_in_threadpool(self._get_block, block_id)
        node = {k: meta[k] for k in ("id", "type", "has_children") if k in meta}
        t = meta["type"]
        node[t] = meta.get(t, {})
        node["children"] = []

        # children
        if meta.get("has_children"):
            cursor = None
            while True:
                res = await run_in_threadpool(self._list_children, block_id, cursor)
                for c in res.get("results", []):
                    sub = await self._snapshot_tree(c["id"])
                    node["children"].append(sub)
                if not res.get("has_more"):
                    break
                cursor = res.get("next_cursor")
        return node