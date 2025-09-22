import os
import json
import html
import shutil
from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, Body, Request
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse, StreamingResponse

from ..deps import require_settings
from ..config import Settings
from ..dump_service import NotionDumpService
from ..migrate_service import NotionMigrateService
from ..utils_id import normalize_notion_id

router = APIRouter(prefix="/api", tags=["api"])

def _validate_dump_path(base_root: str, name: str, subpath: str = "") -> str:
    """
    Validate and return absolute path for dump operations, preventing path traversal.
    Returns the validated absolute path.
    Raises HTTPException if path is invalid.
    """
    base = os.path.abspath(base_root)
    if subpath:
        target = os.path.abspath(os.path.join(base, name, subpath))
        base_name = os.path.abspath(os.path.join(base, name))
        if not target.startswith(base_name + os.sep) and target != base_name:
            raise HTTPException(400, "invalid path")
    else:
        target = os.path.abspath(os.path.join(base, name))
        if not target.startswith(base + os.sep) and target != base:
            raise HTTPException(status_code=400, detail="invalid dump name")
    return target

def _dump_entries(root: str, settings: Settings) -> List[Dict[str, Any]]:
    os.makedirs(root, exist_ok=True)
    items = sorted([d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))])
    out: List[Dict[str, Any]] = []
    for name in items:
        dump_dir = os.path.join(root, name)
        manifest_path = os.path.join(dump_dir, "manifest.json")
        tree_path = os.path.join(dump_dir, "tree.json")
        ready = os.path.exists(manifest_path) and os.path.exists(tree_path)
        out.append({
            "name": name,
            "ready": ready,
            "static_url": f"{settings.STATIC_BASE_URL.rstrip('/')}/{name}/",
            "browse_url": f"/api/browse/{name}/",
        })
    return out

@router.get("/dumps")
def list_dumps(
    detail: int = Query(0, description="1로 주면 상세 항목 반환"),
    settings: Settings = Depends(require_settings)
):
    root = settings.DUMP_ROOT
    os.makedirs(root, exist_ok=True)
    if detail:
        return {"entries": _dump_entries(root, settings)}
    items = sorted([d for d in os.listdir(root) if os.path.isdir(os.path.join(root,d))])
    return {"root": root, "items": items}

@router.get("/dumps/stream")
async def dumps_stream(
    request: Request,
    detail: int = Query(1),
    settings: Settings = Depends(require_settings),
):
    async def gen():
        payload = {"entries": _dump_entries(settings.DUMP_ROOT, settings)}
        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        import asyncio
        try:
            while True:
                if await request.is_disconnected():
                    break
                await asyncio.sleep(15)
                yield "event: ping\ndata: {}\n\n"
        except Exception:
            pass
    return StreamingResponse(gen(), media_type="text/event-stream")

@router.post("/dump")
async def dump_now(page_id: str = Body(..., embed=True),
                   settings: Settings = Depends(require_settings)):
    norm_id = normalize_notion_id(page_id)
    svc = NotionDumpService(settings)
    path = await svc.dump_page_tree(norm_id)
    return {"ok": True, "dump_path": path}

@router.get("/dump/{name}/download")
def download_manifest(name: str, settings: Settings = Depends(require_settings)):
    path = os.path.join(settings.DUMP_ROOT, name, "manifest.json")
    if not os.path.exists(path):
        raise HTTPException(404, "manifest not found")
    return FileResponse(path, media_type="application/json", filename=f"{name}_manifest.json")

def _build_asset_map_from_manifest(manifest: Dict[str, Any], settings: Settings) -> Dict[str, List[Dict[str, str]]]:
    """
    manifest.nodes[*].files[] -> Build upload map by node_id
    files item example:
      { "url": "...", "path": "<dump_root_relative>", "original": "IMG_0001.png", "saved": "abcd.png" }
    """
    amap: Dict[str, List[Dict[str, str]]] = {}
    for node in manifest.get("nodes", []):
        nid = node.get("id")
        files = node.get("files", [])
        if not nid or not files:
            continue
        lst: List[Dict[str, str]] = []
        for fobj in files:
            rel = fobj.get("path")  # "<dump_name>/<saved>"
            local_path = os.path.join(settings.DUMP_ROOT, rel) if rel else None
            lst.append({
                "local_path": local_path,
                "rel_path": rel or "",
                "original": fobj.get("original") or fobj.get("saved") or "file",
            })
        if lst:
            amap[nid] = lst
    return amap

@router.post("/migrate")
async def migrate_now(
    target_page_id: str = Body(...),
    dump_name: str = Body(...),
    settings: Settings = Depends(require_settings)
):
    # Validate dump_name to prevent path traversal
    dump_name = dump_name.strip()
    if not dump_name or ".." in dump_name or "/" in dump_name or "\\" in dump_name:
        raise HTTPException(status_code=400, detail="invalid dump name")
    
    dump_dir = os.path.join(settings.DUMP_ROOT, dump_name)
    tree_path = os.path.join(dump_dir, "tree.json")
    manifest_path = os.path.join(dump_dir, "manifest.json")

    if not os.path.exists(tree_path):
        raise HTTPException(status_code=404, detail="tree.json not found in dump")
    if not os.path.exists(manifest_path):
        raise HTTPException(status_code=404, detail="manifest.json not found in dump")

    with open(tree_path, "r", encoding="utf-8") as f:
        tree = json.load(f)
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    # Build upload map (without using external URLs)
    asset_map = _build_asset_map_from_manifest(manifest, settings)

    msvc = NotionMigrateService(settings)
    await msvc.migrate_under(target_page_id, tree, asset_map)
    return {"ok": True}

@router.get("/browse/{name}/", response_class=HTMLResponse)
def browse_dump(name: str, settings: Settings = Depends(require_settings)):
    root = os.path.join(settings.DUMP_ROOT, name)
    if not os.path.isdir(root):
        raise HTTPException(404, "dump folder not found")

    rows: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        rel_dir = "" if rel_dir == "." else rel_dir
        rows.append(f"<h4>{html.escape(name + ('/' + rel_dir if rel_dir else ''))}</h4>")
        rows.append("<ul>")
        for d in sorted(dirnames):
            sub = os.path.join(rel_dir, d) if rel_dir else d
            rows.append(f'<li>[DIR] <a href="/api/browse/{name}/{html.escape(sub)}/">{html.escape(d)}</a></li>')
        for fn in sorted(filenames):
            rel = os.path.join(rel_dir, fn) if rel_dir else fn
            file_url = f"/files/{name}/{rel}".replace('\\','/')
            rows.append(f'<li><a href="{html.escape(file_url)}" target="_blank">{html.escape(fn)}</a></li>')
        rows.append("</ul>")
        break

    body = f"""
    <html><head><meta charset="utf-8"><title>Browse {html.escape(name)}</title>
    <style>body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;padding:16px}}</style>
    </head><body>
    <h3>Browse: {html.escape(name)}</h3>
    <div>Static file base: <code>{html.escape(settings.STATIC_BASE_URL.rstrip('/') + '/' + name + '/')}</code></div>
    {''.join(rows)}
    </body></html>
    """
    return HTMLResponse(content=body)

@router.get("/browse/{name}/{subpath:path}", response_class=HTMLResponse)
def browse_dump_sub(name: str, subpath: str, settings: Settings = Depends(require_settings)):
    target = _validate_dump_path(settings.DUMP_ROOT, name, subpath)
    if not os.path.isdir(target):
        raise HTTPException(404, "folder not found")

    base_name = os.path.abspath(os.path.join(settings.DUMP_ROOT, name))
    rel_root = os.path.relpath(target, base_name)
    rows: List[str] = []
    for dirpath, dirnames, filenames in os.walk(target):
        rel_dir = os.path.relpath(dirpath, base_name)
        rows.append(f"<h4>{html.escape(name + '/' + rel_dir)}</h4>")
        rows.append('<ul>')
        parent = os.path.dirname(rel_dir)
        if rel_dir:
            parent_url = f"/api/browse/{name}/{parent}/" if parent and parent != "." else f"/api/browse/{name}/"
            rows.append(f'<li>[..] <a href="{html.escape(parent_url)}">Up</a></li>')
        for d in sorted(dirnames):
            sub = os.path.join(rel_dir, d)
            rows.append(f'<li>[DIR] <a href="/api/browse/{name}/{html.escape(sub)}/">{html.escape(d)}</a></li>')
        for fn in sorted(filenames):
            rel = os.path.join(rel_dir, fn)
            file_url = f"/files/{name}/{rel}".replace('\\','/')
            rows.append(f'<li><a href="{html.escape(file_url)}" target="_blank">{html.escape(fn)}</a></li>')
        rows.append('</ul>')
        break

    body = f"""
    <html><head><meta charset="utf-8"><title>Browse {html.escape(name)}</title>
    <style>body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;padding:16px}}</style>
    </head><body>
    <h3>Browse: {html.escape(name + '/' + rel_root)}</h3>
    <div>Static file base: <code>{html.escape(settings.STATIC_BASE_URL.rstrip('/') + '/' + name + '/')}</code></div>
    {''.join(rows)}
    </body></html>
    """
    return HTMLResponse(content=body)

@router.delete("/dump/{name}")
def delete_dump(name: str, settings: Settings = Depends(require_settings)):
    target = _validate_dump_path(settings.DUMP_ROOT, name)
    if not os.path.isdir(target):
        raise HTTPException(status_code=404, detail="dump folder not found")
    try:
        shutil.rmtree(target)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="dump folder not found")
    except PermissionError as e:
        raise HTTPException(status_code=409, detail=f"permission denied: {e}")
    except OSError as e:
        raise HTTPException(status_code=409, detail=f"delete failed: {e}")
    return {"ok": True, "deleted": name}