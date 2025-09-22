import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import get_settings
from .routers import api, ui
from .dump_service import NotionDumpService

app = FastAPI(title="Notion Local Backup", version="1.0.0")

settings = get_settings()
os.makedirs(settings.DUMP_ROOT, exist_ok=True)

# 로컬 덤프 디렉터리를 정적 경로로 서빙 → 마이그레이션 시 이 URL을 노션이 가져감
# 예: http://127.0.0.1:8000/files/<dump_name>/...
app.mount("/files", StaticFiles(directory=settings.DUMP_ROOT), name="files")

app.include_router(api.router)
app.include_router(ui.router)

# 자동 덤프 스케줄러
scheduler = BackgroundScheduler(timezone="Asia/Seoul")
if settings.AUTO_DUMP_PAGE_ID:
    # CRON: "M H D M W" 형식
    m, h, d, mo, w = settings.CRON.split()
    trigger = CronTrigger(minute=m, hour=h, day=d, month=mo, day_of_week=w)
    svc = NotionDumpService(settings)

    def job():
        try:
            import asyncio
            asyncio.run(svc.dump_page_tree(settings.AUTO_DUMP_PAGE_ID))
        except Exception as e:
            print("[AUTO_DUMP] error:", e)

    scheduler.add_job(job, trigger=trigger, id="auto_dump", replace_existing=True)
    scheduler.start()

@app.get("/health")
def health():
    return {"ok": True}