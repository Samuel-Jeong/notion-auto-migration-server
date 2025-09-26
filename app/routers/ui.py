from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from ..config import Settings
from ..deps import require_settings
from ..dump_service import NotionDumpService
from ..migrate_service import NotionMigrateService
import os, json
import re
from datetime import datetime
from ..utils_id import normalize_notion_id

router = APIRouter(tags=["ui"])
templates = Jinja2Templates(directory="app/templates")

def get_dump_metadata(dump_name: str, dump_path: str) -> dict:
    """Extract comprehensive metadata from dump for intelligent selection."""
    metadata = {
        "name": dump_name,
        "path": dump_path,
        "timestamp": extract_timestamp(dump_name),
        "size": 0,
        "file_count": 0,
        "type": "unknown",
        "pages": 0,
        "images": 0,
        "attachments": 0,
        "description": "",
        "tags": []
    }
    
    # Get directory size and file count
    try:
        total_size = 0
        file_count = 0
        image_count = 0
        attachment_count = 0
        
        for root, dirs, files in os.walk(dump_path):
            for file in files:
                file_path = os.path.join(root, file)
                if os.path.exists(file_path):
                    file_size = os.path.getsize(file_path)
                    total_size += file_size
                    file_count += 1
                    
                    # Count image and attachment files
                    ext = os.path.splitext(file)[1].lower()
                    if ext in ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.webp']:
                        image_count += 1
                    elif ext in ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.zip', '.rar']:
                        attachment_count += 1
        
        metadata["size"] = total_size
        metadata["file_count"] = file_count
        metadata["images"] = image_count
        metadata["attachments"] = attachment_count
        
    except Exception:
        pass
    
    # Read manifest for detailed info
    manifest_path = os.path.join(dump_path, "manifest.json")
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
                metadata["pages"] = len(manifest.get("nodes", []))
                metadata["type"] = manifest.get("type", "page")
                metadata["description"] = manifest.get("root_title", "")
                
                # Extract tags from dump name and content
                if "database" in dump_name.lower() or metadata["type"] == "database":
                    metadata["tags"].append("데이터베이스")
                if "page" in dump_name.lower() or metadata["type"] == "page":
                    metadata["tags"].append("페이지")
                if metadata["images"] > 0:
                    metadata["tags"].append("이미지")
                if metadata["attachments"] > 0:
                    metadata["tags"].append("첨부파일")
        except Exception:
            pass
    
    return metadata

def get_group_key(name: str) -> str:
    """Get the grouping key for Korean alphabetical sorting."""
    if not name:
        return "기타"
    
    first_char = name[0]
    
    # Korean consonants (초성)
    if '가' <= first_char <= '힣':
        # Extract initial consonant from Hangul syllable
        code = ord(first_char) - ord('가')
        initial = code // (21 * 28)
        # Correct Korean consonant order based on Unicode Hangul syllable structure
        consonants = ['ㄱ', 'ㄲ', 'ㄴ', 'ㄷ', 'ㄸ', 'ㄹ', 'ㅁ', 'ㅂ', 'ㅃ', 'ㅅ', 'ㅆ', 'ㅇ', 'ㅈ', 'ㅉ', 'ㅊ', 'ㅋ', 'ㅌ', 'ㅍ', 'ㅎ']
        if initial < len(consonants):
            return consonants[initial]
        return 'ㄱ'
    
    # English letters
    elif first_char.isascii() and first_char.isalpha():
        return first_char.upper()
    
    # Numbers
    elif first_char.isdigit():
        return '0-9'
    
    # Other characters
    else:
        return '기타'

def filter_and_sort_dumps(dumps: list, search: str = "", sort_by: str = "timestamp", 
                         filter_type: str = "", min_size: int = 0, max_size: int = 0) -> list:
    """Apply intelligent filtering and sorting to dumps."""
    filtered_dumps = dumps.copy()
    
    # Apply search filter
    if search:
        search_lower = search.lower()
        filtered_dumps = [
            dump for dump in filtered_dumps
            if (search_lower in dump["name"].lower() or 
                search_lower in dump.get("description", "").lower() or
                any(search_lower in tag.lower() for tag in dump.get("tags", [])))
        ]
    
    # Apply type filter
    if filter_type:
        if filter_type == "page":
            filtered_dumps = [d for d in filtered_dumps if "페이지" in d.get("tags", [])]
        elif filter_type == "database":
            filtered_dumps = [d for d in filtered_dumps if "데이터베이스" in d.get("tags", [])]
        elif filter_type == "with_images":
            filtered_dumps = [d for d in filtered_dumps if d.get("images", 0) > 0]
        elif filter_type == "with_attachments":
            filtered_dumps = [d for d in filtered_dumps if d.get("attachments", 0) > 0]
    
    # Apply size filter
    if min_size > 0:
        filtered_dumps = [d for d in filtered_dumps if d.get("size", 0) >= min_size]
    if max_size > 0:
        filtered_dumps = [d for d in filtered_dumps if d.get("size", 0) <= max_size]
    
    # Apply sorting
    if sort_by == "timestamp":
        filtered_dumps.sort(key=lambda x: x.get("timestamp", datetime(1900, 1, 1)), reverse=True)
    elif sort_by == "name":
        filtered_dumps.sort(key=lambda x: x["name"])
    elif sort_by == "size":
        filtered_dumps.sort(key=lambda x: x.get("size", 0), reverse=True)
    elif sort_by == "files":
        filtered_dumps.sort(key=lambda x: x.get("file_count", 0), reverse=True)
    elif sort_by == "pages":
        filtered_dumps.sort(key=lambda x: x.get("pages", 0), reverse=True)
    
    return filtered_dumps

def extract_timestamp(dump_name: str) -> datetime:
    """Extract timestamp from dump name for sorting by time."""
    # Pattern: name_YYYYMMDD_HHMMSS
    pattern = r'_(\d{8})_(\d{6})$'
    match = re.search(pattern, dump_name)
    
    if match:
        date_str = match.group(1)  # YYYYMMDD
        time_str = match.group(2)  # HHMMSS
        try:
            # Parse the timestamp
            timestamp_str = date_str + time_str
            return datetime.strptime(timestamp_str, '%Y%m%d%H%M%S')
        except ValueError:
            pass
    
    # Fallback: return a very old date for invalid timestamps
    return datetime(1900, 1, 1)

@router.get("/", response_class=HTMLResponse)
async def index(request: Request, settings: Settings = Depends(require_settings)):
    os.makedirs(settings.DUMP_ROOT, exist_ok=True)
    dump_dirs = sorted([d for d in os.listdir(settings.DUMP_ROOT) if os.path.isdir(os.path.join(settings.DUMP_ROOT, d))])
    
    # Get query parameters for filtering, sorting, and pagination
    search = request.query_params.get("search", "")
    sort_by = request.query_params.get("sort", "timestamp")
    filter_type = request.query_params.get("type", "")
    min_size = int(request.query_params.get("min_size", "0") or "0")
    max_size = int(request.query_params.get("max_size", "0") or "0")
    page = int(request.query_params.get("page", "1") or "1")
    per_page = int(request.query_params.get("per_page", "10") or "10")
    
    # Build detailed dump info with enhanced metadata
    all_dumps = []
    for dump_name in dump_dirs:
        dump_path = os.path.join(settings.DUMP_ROOT, dump_name)
        
        # Get comprehensive metadata
        dump_metadata = get_dump_metadata(dump_name, dump_path)
        
        # Build file listings for backward compatibility
        files = []
        manifest_path = os.path.join(dump_path, "manifest.json")
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
        
        # Combine metadata with file listings
        dump_metadata["files"] = files
        all_dumps.append(dump_metadata)
    
    # Apply intelligent filtering and sorting
    filtered_dumps = filter_and_sort_dumps(all_dumps, search, sort_by, filter_type, min_size, max_size)
    
    # Calculate pagination
    total_items = len(filtered_dumps)
    total_pages = (total_items + per_page - 1) // per_page if total_items > 0 else 1
    
    # Ensure page is within valid range
    page = max(1, min(page, total_pages))
    
    # Apply pagination
    start_index = (page - 1) * per_page
    end_index = start_index + per_page
    paginated_dumps = filtered_dumps[start_index:end_index]
    
    # Group paginated dumps by initial consonant/character
    dump_groups = {}
    for dump in paginated_dumps:
        group_key = get_group_key(dump["name"])
        if group_key not in dump_groups:
            dump_groups[group_key] = []
        dump_groups[group_key].append(dump)
    
    # Sort groups and dumps within groups
    korean_order = ['ㄱ', 'ㄲ', 'ㄴ', 'ㄷ', 'ㄸ', 'ㄹ', 'ㅁ', 'ㅂ', 'ㅃ', 'ㅅ', 'ㅆ', 'ㅇ', 'ㅈ', 'ㅉ', 'ㅊ', 'ㅋ', 'ㅌ', 'ㅍ', 'ㅎ']
    sorted_groups = []
    
    # Add Korean consonant groups first
    for consonant in korean_order:
        if consonant in dump_groups:
            sorted_groups.append({
                "key": consonant,
                "dumps": dump_groups[consonant]  # Already sorted by filter_and_sort_dumps
            })
    
    # Add English letter groups
    english_groups = sorted([k for k in dump_groups.keys() if k.isascii() and k.isalpha()])
    for group in english_groups:
        sorted_groups.append({
            "key": group,
            "dumps": dump_groups[group]  # Already sorted by filter_and_sort_dumps
        })
    
    # Add number and other groups
    for group in ['0-9', '기타']:
        if group in dump_groups:
            sorted_groups.append({
                "key": group,
                "dumps": dump_groups[group]  # Already sorted by filter_and_sort_dumps
            })
    
    # Keep backward compatibility
    dumps = paginated_dumps
    
    # Prepare filter statistics
    filter_stats = {
        "total_dumps": len(all_dumps),
        "filtered_dumps": len(filtered_dumps),
        "total_size": sum(d.get("size", 0) for d in all_dumps),
        "filtered_size": sum(d.get("size", 0) for d in filtered_dumps),
        "page_dumps": len([d for d in filtered_dumps if "페이지" in d.get("tags", [])]),
        "database_dumps": len([d for d in filtered_dumps if "데이터베이스" in d.get("tags", [])]),
        "with_images": len([d for d in filtered_dumps if d.get("images", 0) > 0]),
        "with_attachments": len([d for d in filtered_dumps if d.get("attachments", 0) > 0])
    }
    
    # Prepare pagination metadata
    pagination = {
        "current_page": page,
        "total_pages": total_pages,
        "per_page": per_page,
        "total_items": total_items,
        "start_item": start_index + 1 if total_items > 0 else 0,
        "end_item": min(end_index, total_items),
        "has_previous": page > 1,
        "has_next": page < total_pages,
        "previous_page": page - 1 if page > 1 else None,
        "next_page": page + 1 if page < total_pages else None
    }
    
    return templates.TemplateResponse("index.html", {
        "request": request,
        "dumps": dumps,
        "dump_groups": sorted_groups,
        "filter_stats": filter_stats,
        "pagination": pagination,
        "current_search": search,
        "current_sort": sort_by,
        "current_type": filter_type,
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

@router.post("/ui/delete", response_class=RedirectResponse)
async def ui_delete_dump(dump_name: str = Form(...),
                         settings: Settings = Depends(require_settings)):
    # Validate dump_name to prevent path traversal
    dump_name = dump_name.strip()
    if not dump_name or ".." in dump_name or "/" in dump_name or "\\" in dump_name:
        return RedirectResponse(url="/?err=invalid_dump_name", status_code=303)
    
    dump_path = os.path.join(settings.DUMP_ROOT, dump_name)
    if not os.path.isdir(dump_path):
        return RedirectResponse(url="/?err=dump_not_found", status_code=303)
    
    try:
        import shutil
        shutil.rmtree(dump_path)
        return RedirectResponse(url="/?ok=deleted", status_code=303)
    except FileNotFoundError:
        return RedirectResponse(url="/?err=dump_not_found", status_code=303)
    except PermissionError:
        return RedirectResponse(url="/?err=permission_denied", status_code=303)
    except OSError:
        return RedirectResponse(url="/?err=delete_failed", status_code=303)