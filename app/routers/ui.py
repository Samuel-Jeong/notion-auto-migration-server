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
    dumps = sorted([d for d in os.listdir(settings.DUMP_ROOT) if os.path.isdir(os.path.join(settings.DUMP_ROOT, d))])
    return templates.TemplateResponse("index.html", {
        "request": request,
        "dumps": dumps,
        "static_base": settings.STATIC_BASE_URL
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
    if not os.path.exists(tree_path):
        return RedirectResponse(url="/?err=tree_not_found", status_code=303)
    with open(tree_path, "r", encoding="utf-8") as f:
        tree = json.load(f)
    msvc = NotionMigrateService(settings)
    await msvc.migrate_under(target_page_id, tree, settings.STATIC_BASE_URL)
    return RedirectResponse(url="/?ok=migrated", status_code=303)