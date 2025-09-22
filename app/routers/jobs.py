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