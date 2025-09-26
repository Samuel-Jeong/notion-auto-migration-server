# app/jobs.py
import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Callable

from .config import Settings
from .dump_service import NotionDumpService
from .migrate_service import NotionMigrateService
from .history_service import JobHistoryService

@dataclass
class Job:
    id: str
    type: str  # "dump" | "migrate"
    status: str = "queued"  # queued | running | done | error | canceled
    progress: int = 0
    message: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=lambda: time.time())
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    task: Optional[asyncio.Task] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "params": self.params,
            "created_at": self.created_at,
        }

class JobManager:
    def __init__(self, settings: Settings, max_dump: int = 3, max_migrate: int = 3):
        self.settings = settings
        self.max_dump = max_dump
        self.max_migrate = max_migrate

        self._jobs: Dict[str, Job] = {}
        self._lock = asyncio.Lock()

        # SSE subscribers (each asyncio.Queue)
        self._subscribers: List[asyncio.Queue] = []
        
        # History service for job tracking
        self.history = JobHistoryService()

    # ─────────────────────────────────────────────────────
    # SSE
    # ─────────────────────────────────────────────────────
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.append(q)
        # Push snapshot on first subscription
        asyncio.create_task(q.put({"kind": "snapshot", "items": [j.to_dict() for j in self._jobs.values()]}))
        return q

    def unsubscribe(self, q: asyncio.Queue):
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    async def _broadcast(self, payload: Dict[str, Any]):
        for q in list(self._subscribers):
            try:
                await q.put(payload)
            except Exception:
                pass

    async def _broadcast_added(self, job: Job):
        await self._broadcast({"kind": "job_added", "job": job.to_dict()})

    async def _broadcast_update(self, job: Job):
        await self._broadcast({"kind": "job_update", "job": job.to_dict()})

    # ─────────────────────────────────────────────────────
    # Capacity check
    # ─────────────────────────────────────────────────────
    async def _count_active(self, typ: str) -> int:
        # Consider queued + running as active
        return sum(1 for j in self._jobs.values() if j.type == typ and j.status in ("queued", "running"))

    async def _ensure_capacity(self, typ: str):
        limit = self.max_dump if typ == "dump" else self.max_migrate
        if await self._count_active(typ) >= limit:
            raise RuntimeError(f"{typ} jobs are at capacity (max {limit}). Please try again later.")

    # ─────────────────────────────────────────────────────
    # Common: cancel/remove/snapshot
    # ─────────────────────────────────────────────────────
    async def cancel(self, job_id: str) -> bool:
        async with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False
            if job.status in ("done", "error", "canceled"):
                return True
            job.cancel_event.set()
            job.status = "canceled"
            job.message = "Cancelled"
            await self._broadcast_update(job)
            
            # Log cancellation to history
            await self.history.update_job_progress(
                job_id=job.id,
                status="canceled",
                message="Cancelled by user"
            )
            return True

    async def remove(self, job_id: str) -> bool:
        async with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False
            if job.status in ("queued", "running"):
                return False  # Cannot remove running/queued jobs (cancel first)
            self._jobs.pop(job_id, None)
            # Use snapshot instead of removal notification (simplified)
            await self._broadcast({"kind": "snapshot", "items": [j.to_dict() for j in self._jobs.values()]})
            return True

    def list_jobs(self) -> List[Dict[str, Any]]:
        return [j.to_dict() for j in sorted(self._jobs.values(), key=lambda x: x.created_at, reverse=True)]

    # ─────────────────────────────────────────────────────
    # Progress/message update helper
    # ─────────────────────────────────────────────────────
    async def _tick(self, job: Job, p: Optional[int] = None, m: Optional[str] = None):
        if p is not None:
            job.progress = max(0, min(100, p))
        if m is not None:
            job.message = m
        await self._broadcast_update(job)
        
        # Log progress to history
        await self.history.update_job_progress(
            job_id=job.id,
            status=job.status,
            progress=job.progress,
            message=job.message
        )

    async def _auto_cleanup_job(self, job: Job):
        """Automatically remove a job after it completes (done/error/canceled status)"""
        if job.status in ("done", "error", "canceled"):
            # Wait 3 seconds before cleaning up to allow users to see completion status
            await asyncio.sleep(3.0)
            async with self._lock:
                if job.id in self._jobs:
                    self._jobs.pop(job.id, None)
                    # Broadcast snapshot to update UI
                    await self._broadcast({"kind": "snapshot", "items": [j.to_dict() for j in self._jobs.values()]})

    # ─────────────────────────────────────────────────────
    # Dump operations
    # ─────────────────────────────────────────────────────
    async def enqueue_dump(self, page_id: str) -> Job:
        await self._ensure_capacity("dump")
        job = Job(id=str(uuid.uuid4()), type="dump", params={"page_id": page_id})
        async with self._lock:
            self._jobs[job.id] = job
        await self._broadcast_added(job)
        
        # Log job creation to history
        await self.history.add_job_started(
            job_id=job.id,
            job_type="dump",
            page_id=page_id
        )

        async def runner():
            # Check if job was already canceled before starting
            if job.cancel_event.is_set():
                return  # Job already canceled, don't override status
                
            job.status = "running"
            await self._tick(job, 0, "Starting")
            svc = NotionDumpService(self.settings)
            try:
                def _progress(p: int, msg: str):
                    # Called from background → schedule safely in event loop
                    asyncio.get_event_loop().create_task(self._tick(job, p, msg))

                def _cancelled() -> bool:
                    return job.cancel_event.is_set()

                path = await svc.dump_page_tree(page_id, progress_cb=_progress, cancel_cb=_cancelled)
                if job.cancel_event.is_set() and job.status != "canceled":
                    job.status = "canceled"
                    await self._tick(job, job.progress, "Cancelled")
                    await self._auto_cleanup_job(job)
                elif not job.cancel_event.is_set():
                    job.status = "done"
                    await self._tick(job, 100, f"Complete: {path}")
                    await self._auto_cleanup_job(job)
            except asyncio.CancelledError:
                if job.status != "canceled":
                    job.status = "canceled"
                    await self._tick(job, job.progress, "Cancelled")
                    await self._auto_cleanup_job(job)
            except Exception as e:
                job.status = "error"
                await self._tick(job, job.progress, f"Error: {e}")
                await self._auto_cleanup_job(job)

        job.task = asyncio.create_task(runner())
        return job

    async def enqueue_dump_database(self, database_id: str) -> Job:
        await self._ensure_capacity("dump")
        job = Job(id=str(uuid.uuid4()), type="dump_database", params={"database_id": database_id})
        async with self._lock:
            self._jobs[job.id] = job
        await self._broadcast_added(job)
        
        # Log job creation to history
        await self.history.add_job_started(
            job_id=job.id,
            job_type="dump_database",
            database_id=database_id
        )

        async def runner():
            # Check if job was already canceled before starting
            if job.cancel_event.is_set():
                return  # Job already canceled, don't override status
                
            job.status = "running"
            await self._tick(job, 0, "Starting database dump")
            svc = NotionDumpService(self.settings)
            try:
                def _progress(p: int, msg: str):
                    # Called from background → schedule safely in event loop
                    asyncio.get_event_loop().create_task(self._tick(job, p, msg))

                def _cancelled() -> bool:
                    return job.cancel_event.is_set()

                path = await svc.dump_database_tree(database_id, progress_cb=_progress, cancel_cb=_cancelled)
                if job.cancel_event.is_set() and job.status != "canceled":
                    job.status = "canceled"
                    await self._tick(job, job.progress, "Cancelled")
                    await self._auto_cleanup_job(job)
                elif not job.cancel_event.is_set():
                    job.status = "done"
                    await self._tick(job, 100, f"Complete: {path}")
                    await self._auto_cleanup_job(job)
            except asyncio.CancelledError:
                if job.status != "canceled":
                    job.status = "canceled"
                    await self._tick(job, job.progress, "Cancelled")
                    await self._auto_cleanup_job(job)
            except Exception as e:
                job.status = "error"
                await self._tick(job, job.progress, f"Error: {e}")
                await self._auto_cleanup_job(job)

        job.task = asyncio.create_task(runner())
        return job

    # ─────────────────────────────────────────────────────
    # Migration operations
    # ─────────────────────────────────────────────────────
    async def enqueue_migrate(self, dump_name: str, target_page_id: str) -> Job:
        await self._ensure_capacity("migrate")
        job = Job(id=str(uuid.uuid4()), type="migrate", params={"dump_name": dump_name, "target_page_id": target_page_id})
        async with self._lock:
            self._jobs[job.id] = job
        await self._broadcast_added(job)
        
        # Log job creation to history
        await self.history.add_job_started(
            job_id=job.id,
            job_type="migrate",
            dump_name=dump_name,
            target_page_id=target_page_id
        )

        async def runner():
            # Check if job was already canceled before starting
            if job.cancel_event.is_set():
                return  # Job already canceled, don't override status
                
            job.status = "running"
            await self._tick(job, 0, "Starting")
            svc = NotionMigrateService(self.settings)
            try:
                def _progress(p: int, msg: str):
                    asyncio.get_event_loop().create_task(self._tick(job, p, msg))

                def _cancelled() -> bool:
                    return job.cancel_event.is_set()

                # Build tree and asset map from dump files
                import os
                import json
                tree_path = os.path.join(self.settings.DUMP_ROOT, dump_name, "tree.json")
                manifest_path = os.path.join(self.settings.DUMP_ROOT, dump_name, "manifest.json")
                
                if not os.path.exists(tree_path) or not os.path.exists(manifest_path):
                    raise FileNotFoundError("Required dump files not found")
                
                with open(tree_path, "r", encoding="utf-8") as f:
                    tree = json.load(f)
                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
                
                # Import the helper function to build asset map
                from .routers.api import _build_asset_map_from_manifest
                asset_map = _build_asset_map_from_manifest(manifest, self.settings)

                # Detect if this is a database or page dump
                tree_type = tree.get("type", "root")
                if tree_type == "database":
                    # This is a database dump, use database migration with asset_map
                    await svc.migrate_database_under(target_page_id, tree, asset_map, progress_cb=_progress, cancel_cb=_cancelled)
                else:
                    # This is a page dump, use regular migration
                    await svc.migrate_under(target_page_id, tree, asset_map, progress_cb=_progress, cancel_cb=_cancelled)
                if job.cancel_event.is_set() and job.status != "canceled":
                    job.status = "canceled"
                    await self._tick(job, job.progress, "Cancelled")
                    await self._auto_cleanup_job(job)
                elif not job.cancel_event.is_set():
                    job.status = "done"
                    await self._tick(job, 100, "Complete")
                    await self._auto_cleanup_job(job)
            except asyncio.CancelledError:
                if job.status != "canceled":
                    job.status = "canceled"
                    await self._tick(job, job.progress, "Cancelled")
                    await self._auto_cleanup_job(job)
            except Exception as e:
                job.status = "error"
                await self._tick(job, job.progress, f"Error: {e}")
                await self._auto_cleanup_job(job)

        job.task = asyncio.create_task(runner())
        return job