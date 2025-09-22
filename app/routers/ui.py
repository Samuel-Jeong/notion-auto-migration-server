from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from ..config import Settings
from ..deps import require_settings
from ..dump_service import NotionDumpService
from ..migrate_service import NotionMigrateService
import os, json
from ..utils_id import normalize_notion_id

router = APIRouter(tags=["ui"])
templates = Jinja2Templates(directory="app/templates")

@router.get("/", response_class=HTMLResponse)
async def index(request: Request, settings: Settings = Depends(require_settings)):
    os.makedirs(settings.DUMP_ROOT, exist_ok=True)
    dump_dirs = sorted([d for d in os.listdir(settings.DUMP_ROOT) if os.path.isdir(os.path.join(settings.DUMP_ROOT, d))])
    
    # Build detailed dump info with file listings
    dumps = []
    for dump_name in dump_dirs:
        dump_path = os.path.join(settings.DUMP_ROOT, dump_name)
        manifest_path = os.path.join(dump_path, "manifest.json")
        
        files = []
        if os.path.exists(manifest_path):
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
                    # Extract file paths from manifest
                    for node in manifest.get("nodes", []):
                        for file_info in node.get("files", []):
                            file_path = file_info.get("path", "")
                            if file_path:
                                files.append({
                                    "path": file_path,
                                    "original": file_info.get("original", ""),
                                    "url": f"/files/{file_path}"
                                })
            except Exception:
                pass
        
        # Also add manifest and tree files
        if os.path.exists(manifest_path):
            files.append({
                "path": f"{dump_name}/manifest.json",
                "original": "manifest.json",
                "url": f"/files/{dump_name}/manifest.json"
            })
        
        tree_path = os.path.join(dump_path, "tree.json")
        if os.path.exists(tree_path):
            files.append({
                "path": f"{dump_name}/tree.json", 
                "original": "tree.json",
                "url": f"/files/{dump_name}/tree.json"
            })
        
        dumps.append({
            "name": dump_name,
            "files": files
        })
    
    return templates.TemplateResponse("index.html", {
        "request": request,
        "dumps": dumps,
        "static_base": settings.STATIC_BASE_URL,
    })

@router.post("/ui/dump", response_class=RedirectResponse)
async def ui_dump(page_id: str = Form(...),
                  settings: Settings = Depends(require_settings)):
    try:
        norm_id = normalize_notion_id(page_id)
    except ValueError:
        return RedirectResponse(url="/?err=invalid_page_id", status_code=303)

    svc = NotionDumpService(settings)
    await svc.dump_page_tree(norm_id)
    return RedirectResponse(url="/?ok=dumped", status_code=303)

@router.post("/ui/migrate", response_class=RedirectResponse)
async def ui_migrate(target_page_id: str = Form(...),
                     dump_name: str = Form(...),
                     settings: Settings = Depends(require_settings)):
    tree_path = os.path.join(settings.DUMP_ROOT, dump_name, "tree.json")
    manifest_path = os.path.join(settings.DUMP_ROOT, dump_name, "manifest.json")
    if not os.path.exists(tree_path):
        return RedirectResponse(url="/?err=tree_not_found", status_code=303)
    if not os.path.exists(manifest_path):
        return RedirectResponse(url="/?err=manifest_not_found", status_code=303)
    
    with open(tree_path, "r", encoding="utf-8") as f:
        tree = json.load(f)
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    
    # Import the helper function to build asset map
    from .api import _build_asset_map_from_manifest
    asset_map = _build_asset_map_from_manifest(manifest, settings)
    
    msvc = NotionMigrateService(settings)
    await msvc.migrate_under(target_page_id, tree, asset_map)
    return RedirectResponse(url="/?ok=migrated", status_code=303)