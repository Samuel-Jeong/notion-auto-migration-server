import os, json, glob
from fastapi import APIRouter, Depends, HTTPException, status, Query, Body
from fastapi.responses import JSONResponse, FileResponse
from ..deps import require_settings
from ..config import Settings
from ..dump_service import NotionDumpService
from ..migrate_service import NotionMigrateService
from ..utils_id import normalize_notion_id

router = APIRouter(prefix="/api", tags=["api"])

@router.get("/dumps")
def list_dumps(settings: Settings = Depends(require_settings)):
    root = settings.DUMP_ROOT
    os.makedirs(root, exist_ok=True)
    items = sorted([d for d in os.listdir(root) if os.path.isdir(os.path.join(root,d))])
    return {"root": root, "items": items}

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


@router.post("/migrate")
async def migrate_now(
    target_page_id: str = Body(...),
    dump_name: str = Body(...),
    settings: Settings = Depends(require_settings)
):
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

    # 블록ID -> [정적URL들] 맵 구성
    # manifest["nodes"]의 각 node에 files:[{"path": "..."}] 가 들어있음
    # STATIC_BASE_URL + "/" + path 로 외부 접근 가능
    asset_url_map: Dict[str, List[str]] = {}
    static_base = settings.STATIC_BASE_URL.rstrip("/")
    for node in manifest.get("nodes", []):
        nid = node.get("id")
        files = node.get("files", [])
        if not nid or not files:
            continue
        urls = [f"{static_base}/{fobj['path']}" for fobj in files if fobj.get("path")]
        if urls:
            asset_url_map[nid] = urls

    msvc = NotionMigrateService(settings)
    await msvc.migrate_under(target_page_id, tree, asset_url_map)
    return {"ok": True}