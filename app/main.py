# app/main.py
import os
import logging
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import get_settings
from .routers import api, ui
from .routers import jobs as jobs_router
from .dump_service import NotionDumpService
from .jobs import JobManager
from .routers.jobs import get_manager as get_jobs_manager  # Share same instance

app = FastAPI(title="Notion Local Backup", version="1.2.0")
settings = get_settings()

# Logging configuration: File rotation + Console
LOG_DIR = os.environ.get("LOG_DIR", "/app/_logs")
os.makedirs(LOG_DIR, exist_ok=True)

def _mk_rotating_handler(path: str, level: int, fmt: str) -> RotatingFileHandler:
    h = RotatingFileHandler(path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
    h.setLevel(level)
    h.setFormatter(logging.Formatter(fmt))
    return h

# Common log level
LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

# App logger (application code)
app_logger = logging.getLogger("app")
if not app_logger.handlers:
    app_logger.setLevel(LEVEL)
    app_logger.addHandler(_mk_rotating_handler(
        os.path.join(LOG_DIR, "app.log"),
        getattr(logging, LEVEL, logging.INFO),
        "[%(asctime)s] %(levelname)s %(name)s: %(message)s"
    ))
    # Also maintain console logging
    sh = logging.StreamHandler()
    sh.setLevel(getattr(logging, LEVEL, logging.INFO))
    sh.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s"))
    app_logger.addHandler(sh)

# Also log uvicorn error/access to files
uv_err = logging.getLogger("uvicorn.error")
if not any(isinstance(h, RotatingFileHandler) for h in uv_err.handlers):
    uv_err.addHandler(_mk_rotating_handler(
        os.path.join(LOG_DIR, "uvicorn.log"),
        getattr(logging, LEVEL, logging.INFO),
        "[%(asctime)s] %(levelname)s %(name)s: %(message)s"
    ))

uv_acc = logging.getLogger("uvicorn.access")
if not any(isinstance(h, RotatingFileHandler) for h in uv_acc.handlers):
    uv_acc.addHandler(_mk_rotating_handler(
        os.path.join(LOG_DIR, "access.log"),
        getattr(logging, LEVEL, logging.INFO),
        "%(asctime)s %(message)s"
    ))

logger = logging.getLogger("app.main")

# Static files - add route for directory listing before StaticFiles mount
os.makedirs(settings.DUMP_ROOT, exist_ok=True)

@app.get("/files/")
async def files_directory_listing():
    """Provide helpful information when users access /files/ directory"""
    import os
    # List available dumps
    try:
        dump_dirs = sorted([d for d in os.listdir(settings.DUMP_ROOT) 
                           if os.path.isdir(os.path.join(settings.DUMP_ROOT, d))])
        
        html_content = """
        <html>
        <head>
            <meta charset="utf-8">
            <title>Files Directory</title>
            <style>
                body { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; padding: 16px; }
                .info { background: #e3f2fd; padding: 12px; border-radius: 8px; margin-bottom: 16px; }
                ul { list-style-type: none; padding: 0; }
                li { margin: 8px 0; }
                a { text-decoration: none; color: #1976d2; }
                a:hover { text-decoration: underline; }
            </style>
        </head>
        <body>
            <h2>Files Directory</h2>
            <div class="info">
                <p><strong>Note:</strong> Direct directory browsing is not available at /files/</p>
                <p>To browse dump contents, use the browse API endpoints below:</p>
            </div>
            <h3>Available Dumps:</h3>
        """
        
        if dump_dirs:
            html_content += "<ul>"
            for dump_name in dump_dirs:
                html_content += f'<li><a href="/api/browse/{dump_name}/" target="_blank">📁 {dump_name}</a> - Browse contents</li>'
            html_content += "</ul>"
        else:
            html_content += "<p>No dumps available yet. Create a dump from the main page.</p>"
            
        html_content += """
            <p><a href="/">&larr; Back to main page</a></p>
        </body>
        </html>
        """
        
        from fastapi.responses import HTMLResponse
        return HTMLResponse(content=html_content)
        
    except Exception as e:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=500,
            content={"detail": f"Error listing dumps: {str(e)}"}
        )

@app.get("/files/{dump_name}/")
async def files_dump_directory(dump_name: str):
    """Handle URL-encoded dump directory requests by redirecting to browse API"""
    import urllib.parse
    from fastapi.responses import RedirectResponse
    
    # Decode URL-encoded dump name
    decoded_name = urllib.parse.unquote(dump_name)
    
    # Check if the dump directory exists
    dump_path = os.path.join(settings.DUMP_ROOT, decoded_name)
    if os.path.exists(dump_path) and os.path.isdir(dump_path):
        # Redirect to the browse API endpoint
        browse_url = f"/api/browse/{urllib.parse.quote(decoded_name, safe='')}/"
        return RedirectResponse(url=browse_url, status_code=302)
    
    # If dump doesn't exist, return helpful 404
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=404,
        content={"detail": f"Dump directory '{decoded_name}' not found"}
    )

app.mount("/files", StaticFiles(directory=settings.DUMP_ROOT), name="files")

# Routers
app.include_router(api.router)
app.include_router(ui.router)
app.include_router(jobs_router.router)

# JobManager injection
app.state.jobman = JobManager(settings, max_dump=3, max_migrate=3)

# Auto dump scheduler
scheduler = BackgroundScheduler(timezone="Asia/Seoul")

# Queue for communication between scheduler thread and main async loop
import asyncio
scheduler_queue: asyncio.Queue = asyncio.Queue()

def _effective_auto_ids():
    ids = []
    if hasattr(settings, "auto_dump_ids") and callable(getattr(settings, "auto_dump_ids")):
        ids.extend(settings.auto_dump_ids())
    else:
        if getattr(settings, "AUTO_DUMP_PAGE_ID", ""):
            ids.append(settings.AUTO_DUMP_PAGE_ID)
    # dedup
    out, seen = [], set()
    for v in ids:
        s = (v or "").strip()
        if s and s not in seen:
            out.append(s); seen.add(s)
    return out

def _build_cron_trigger(expr: str) -> CronTrigger:
    try:
        return CronTrigger.from_crontab(expr, timezone="Asia/Seoul")
    except Exception:
        pass
    parts = (expr or "").split()
    if len(parts) != 5:
        raise ValueError(f"Invalid CRON expression: '{expr}' (example: '0 * * * *')")
    m, h, d, mo, w = parts
    return CronTrigger(minute=m, hour=h, day=d, month=mo, day_of_week=w, timezone="Asia/Seoul")

def _auto_dump_job():
    """Called by APScheduler from background thread - put request in queue for main loop to process"""
    try:
        ids = _effective_auto_ids()
        if not ids:
            logger.info("[AUTO_DUMP] No target page IDs found. Skipping.")
            return
        # Put the dump request in the queue for async processing
        scheduler_queue.put_nowait({"type": "auto_dump", "page_ids": ids})
        logger.info(f"[AUTO_DUMP] Queued auto dump request for {len(ids)} page(s)")
    except Exception as e:
        logger.exception(f"[AUTO_DUMP] Error queuing auto dump: {e}")

async def _process_scheduler_queue():
    """Background task that processes scheduler requests from the queue"""
    logger.info("[AUTO_DUMP] Scheduler queue processor started")
    while True:
        try:
            # Wait for scheduler requests
            request = await scheduler_queue.get()
            if request.get("type") == "auto_dump":
                page_ids = request.get("page_ids", [])
                logger.info(f"[AUTO_DUMP] Processing auto dump request for {len(page_ids)} page(s)")
                
                mgr = await get_jobs_manager(settings)
                for pid in page_ids:
                    try:
                        await mgr.enqueue_dump(pid)
                        logger.info(f"[AUTO_DUMP] queued: {pid}")
                    except Exception as e:
                        logger.exception(f"[AUTO_DUMP] enqueue failed: {pid} err={e}")
            
            scheduler_queue.task_done()
        except Exception as e:
            logger.exception(f"[AUTO_DUMP] Scheduler queue processor error: {e}")
            await asyncio.sleep(5)  # Wait before retrying

def _maybe_start_scheduler():
    ids = _effective_auto_ids()
    if not ids:
        logger.info("[AUTO_DUMP] No auto dump targets found, scheduler not started.")
        return
    try:
        trigger = _build_cron_trigger(settings.CRON)
    except Exception as e:
        logger.error(f"[AUTO_DUMP] CRON parsing failed: {settings.CRON} err={e}")
        return
    scheduler.add_job(_auto_dump_job, trigger=trigger, id="auto_dump", replace_existing=True)
    scheduler.start()
    logger.info(f"[AUTO_DUMP] Scheduler started: CRON='{settings.CRON}', targets={len(ids)}")

@app.on_event("startup")
async def _on_startup():
    # Start the background task to process scheduler queue
    asyncio.create_task(_process_scheduler_queue())
    # Start the APScheduler
    _maybe_start_scheduler()

@app.on_event("shutdown")
async def _on_shutdown():
    try:
        if scheduler.running:
            scheduler.shutdown(wait=False)
            logger.info("[AUTO_DUMP] Scheduler shutdown")
    except Exception:
        pass

@app.get("/health")
def health():
    return {"ok": True}