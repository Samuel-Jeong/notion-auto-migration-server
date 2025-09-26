# app/routers/jobs.py
import asyncio
import json
from typing import Any, Dict

from fastapi import APIRouter, Request, Depends, Body, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from ..config import Settings, get_settings
from ..jobs import JobManager

router = APIRouter(prefix="/jobs", tags=["jobs"])

# Get JobManager injected into the app
async def get_manager(settings: Settings = Depends(get_settings)) -> JobManager:
    # Stored in app.state.jobman from main.py
    from ..main import app
    jm: JobManager = app.state.jobman
    return jm

@router.get("")
async def list_jobs(mgr: JobManager = Depends(get_manager)):
    return {"items": mgr.list_jobs()}

@router.post("/dump")
async def create_dump(payload: Dict[str, Any] = Body(...), mgr: JobManager = Depends(get_manager)):
    page_id = (payload.get("page_id") or "").strip()
    if not page_id:
        raise HTTPException(400, "page_id is required")
    job = await mgr.enqueue_dump(page_id)
    return {"ok": True, "job": job.to_dict()}

@router.post("/dump_database")
async def create_dump_database(payload: Dict[str, Any] = Body(...), mgr: JobManager = Depends(get_manager)):
    database_id = (payload.get("database_id") or "").strip()
    if not database_id:
        raise HTTPException(400, "database_id is required")
    job = await mgr.enqueue_dump_database(database_id)
    return {"ok": True, "job": job.to_dict()}

@router.post("/migrate")
async def create_migrate(payload: Dict[str, Any] = Body(...), mgr: JobManager = Depends(get_manager)):
    dump_name = (payload.get("dump_name") or "").strip()
    target_page_id = (payload.get("target_page_id") or "").strip()
    if not dump_name or not target_page_id:
        raise HTTPException(400, "dump_name and target_page_id are required")
    job = await mgr.enqueue_migrate(dump_name, target_page_id)
    return {"ok": True, "job": job.to_dict()}

@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str, mgr: JobManager = Depends(get_manager)):
    ok = await mgr.cancel(job_id)
    if not ok:
        raise HTTPException(404, "job not found")
    return {"ok": True}

@router.post("/{job_id}/remove")
async def remove_job(job_id: str, mgr: JobManager = Depends(get_manager)):
    ok = await mgr.remove(job_id)
    if not ok:
        raise HTTPException(400, "cannot remove (job not finished or not found)")
    return {"ok": True}

@router.get("/stream")
async def stream_jobs(request: Request, mgr: JobManager = Depends(get_manager)):
    q = mgr.subscribe()

    async def gen():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    evt = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    # keep-alive ping
                    yield "event: ping\ndata: {}\n\n"
                    continue
                yield "data: " + json.dumps(evt, ensure_ascii=False) + "\n\n"
        finally:
            mgr.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream")

@router.get("/history")
async def get_job_history(days: int = 7, mgr: JobManager = Depends(get_manager)):
    """Get job history for the last N days"""
    history = await mgr.history.get_recent_history(days=days)
    return {"history": history}

@router.get("/history/dates")
async def get_available_dates(mgr: JobManager = Depends(get_manager)):
    """Get list of available dates with job history"""
    dates = await mgr.history.get_available_dates()
    return {"dates": dates}

@router.get("/history/{date}")
async def get_daily_history(date: str, mgr: JobManager = Depends(get_manager)):
    """Get job history for a specific date (YYYY-MM-DD format)"""
    from datetime import datetime
    try:
        target_date = datetime.strptime(date, "%Y-%m-%d").date()
        jobs = await mgr.history.get_daily_history(target_date)
        return {"date": date, "jobs": jobs}
    except ValueError:
        raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD")

@router.get("/history/range")
async def get_history_range(
    start_date: str, 
    end_date: str, 
    job_type: str = None, 
    status: str = None,
    mgr: JobManager = Depends(get_manager)
):
    """Get job history for a date range with optional filtering"""
    from datetime import datetime
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
        
        if start > end:
            raise HTTPException(400, "start_date must be before or equal to end_date")
        
        history = await mgr.history.get_history_range(start, end, job_type, status)
        return {
            "start_date": start_date,
            "end_date": end_date,
            "job_type": job_type,
            "status": status,
            "history": history
        }
    except ValueError:
        raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD")

@router.get("/statistics")
async def get_job_statistics(days: int = 30, mgr: JobManager = Depends(get_manager)):
    """Get job statistics for the last N days"""
    if days < 1 or days > 365:
        raise HTTPException(400, "days must be between 1 and 365")
    
    stats = await mgr.history.get_job_statistics(days)
    return {"days": days, "statistics": stats}