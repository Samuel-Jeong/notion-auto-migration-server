# app/history_service.py
import json
import os
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
import asyncio


@dataclass
class JobHistoryEntry:
    """Single job history entry"""
    job_id: str
    job_type: str  # 'dump', 'dump_database', 'migrate'
    status: str    # 'queued', 'running', 'done', 'failed', 'canceled'
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    progress: int = 0
    message: str = ""
    page_id: Optional[str] = None
    database_id: Optional[str] = None
    dump_name: Optional[str] = None
    target_page_id: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class JobHistoryService:
    """Service for managing job history with daily file organization"""
    
    def __init__(self, history_root: str = "./_history"):
        self.history_root = Path(history_root)
        self.history_root.mkdir(exist_ok=True)
        self._lock = asyncio.Lock()
    
    def _get_daily_file_path(self, target_date: date = None) -> Path:
        """Get the file path for a specific date's history"""
        if target_date is None:
            target_date = date.today()
        
        filename = f"jobs_{target_date.strftime('%Y%m%d')}.json"
        return self.history_root / filename
    
    async def _load_daily_history(self, target_date: date = None) -> List[Dict[str, Any]]:
        """Load job history for a specific date"""
        file_path = self._get_daily_file_path(target_date)
        
        if not file_path.exists():
            return []
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get('jobs', [])
        except (json.JSONDecodeError, IOError) as e:
            print(f"Error loading history file {file_path}: {e}")
            return []
    
    async def _save_daily_history(self, jobs: List[Dict[str, Any]], target_date: date = None):
        """Save job history for a specific date"""
        async with self._lock:
            file_path = self._get_daily_file_path(target_date)
            
            data = {
                'date': target_date.isoformat() if target_date else date.today().isoformat(),
                'jobs': jobs,
                'last_updated': datetime.now().isoformat()
            }
            
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except IOError as e:
                print(f"Error saving history file {file_path}: {e}")
    
    async def add_job_started(self, job_id: str, job_type: str, **kwargs) -> JobHistoryEntry:
        """Add a new job to today's history when it starts"""
        now = datetime.now().isoformat()
        
        entry = JobHistoryEntry(
            job_id=job_id,
            job_type=job_type,
            status='queued',
            created_at=now,
            page_id=kwargs.get('page_id'),
            database_id=kwargs.get('database_id'),
            dump_name=kwargs.get('dump_name'),
            target_page_id=kwargs.get('target_page_id')
        )
        
        jobs = await self._load_daily_history()
        jobs.append(entry.to_dict())
        await self._save_daily_history(jobs)
        
        return entry
    
    async def update_job_progress(self, job_id: str, status: str = None, progress: int = None, 
                                 message: str = None, error: str = None):
        """Update job progress in today's history"""
        jobs = await self._load_daily_history()
        
        for job in jobs:
            if job['job_id'] == job_id:
                now = datetime.now().isoformat()
                
                if status:
                    job['status'] = status
                    if status == 'running' and not job.get('started_at'):
                        job['started_at'] = now
                    elif status in ['done', 'failed', 'canceled']:
                        job['completed_at'] = now
                
                if progress is not None:
                    job['progress'] = progress
                
                if message is not None:
                    job['message'] = message
                
                if error is not None:
                    job['error'] = error
                
                await self._save_daily_history(jobs)
                break
    
    async def get_daily_history(self, target_date: date = None) -> List[Dict[str, Any]]:
        """Get job history for a specific date"""
        return await self._load_daily_history(target_date)
    
    async def get_recent_history(self, days: int = 7) -> Dict[str, List[Dict[str, Any]]]:
        """Get job history for the last N days"""
        history = {}
        today = date.today()
        
        for i in range(days):
            target_date = date.fromordinal(today.toordinal() - i)
            jobs = await self._load_daily_history(target_date)
            if jobs:  # Only include days with jobs
                history[target_date.isoformat()] = jobs
        
        return history
    
    async def get_history_range(self, start_date: date, end_date: date, 
                               job_type: str = None, status: str = None) -> Dict[str, List[Dict[str, Any]]]:
        """Get job history for a specific date range with optional filtering"""
        history = {}
        current_date = start_date
        
        while current_date <= end_date:
            jobs = await self._load_daily_history(current_date)
            
            # Apply filters if specified
            if jobs and (job_type or status):
                filtered_jobs = []
                for job in jobs:
                    if job_type and job.get('job_type') != job_type:
                        continue
                    if status and job.get('status') != status:
                        continue
                    filtered_jobs.append(job)
                jobs = filtered_jobs
            
            if jobs:  # Only include days with jobs
                history[current_date.isoformat()] = jobs
            
            current_date = date.fromordinal(current_date.toordinal() + 1)
        
        return history
    
    async def get_job_statistics(self, days: int = 30) -> Dict[str, Any]:
        """Get job statistics for the last N days"""
        history = await self.get_recent_history(days)
        
        stats = {
            'total_jobs': 0,
            'by_type': {'dump': 0, 'dump_database': 0, 'migrate': 0},
            'by_status': {'done': 0, 'failed': 0, 'canceled': 0, 'error': 0},
            'success_rate': 0,
            'average_duration': 0,
            'daily_counts': {}
        }
        
        total_duration = 0
        duration_count = 0
        
        for date_str, jobs in history.items():
            daily_count = len(jobs)
            stats['daily_counts'][date_str] = daily_count
            stats['total_jobs'] += daily_count
            
            for job in jobs:
                # Count by type
                job_type = job.get('job_type', 'unknown')
                if job_type in stats['by_type']:
                    stats['by_type'][job_type] += 1
                
                # Count by status
                status = job.get('status', 'unknown')
                if status in stats['by_status']:
                    stats['by_status'][status] += 1
                
                # Calculate duration if available
                started_at = job.get('started_at')
                completed_at = job.get('completed_at')
                if started_at and completed_at:
                    try:
                        start = datetime.fromisoformat(started_at.replace('Z', '+00:00'))
                        end = datetime.fromisoformat(completed_at.replace('Z', '+00:00'))
                        duration = (end - start).total_seconds()
                        total_duration += duration
                        duration_count += 1
                    except (ValueError, TypeError):
                        pass
        
        # Calculate success rate
        successful_jobs = stats['by_status']['done']
        if stats['total_jobs'] > 0:
            stats['success_rate'] = round((successful_jobs / stats['total_jobs']) * 100, 1)
        
        # Calculate average duration
        if duration_count > 0:
            stats['average_duration'] = round(total_duration / duration_count, 1)
        
        return stats
    
    async def get_available_dates(self) -> List[str]:
        """Get list of dates that have job history"""
        dates = []
        
        for file_path in self.history_root.glob("jobs_*.json"):
            try:
                # Extract date from filename (jobs_YYYYMMDD.json)
                date_str = file_path.stem.replace('jobs_', '')
                date_obj = datetime.strptime(date_str, '%Y%m%d').date()
                dates.append(date_obj.isoformat())
            except ValueError:
                continue
        
        return sorted(dates, reverse=True)  # Most recent first
    
    async def cleanup_old_history(self, keep_days: int = 30):
        """Clean up history files older than specified days"""
        cutoff_date = date.fromordinal(date.today().toordinal() - keep_days)
        
        for file_path in self.history_root.glob("jobs_*.json"):
            try:
                date_str = file_path.stem.replace('jobs_', '')
                file_date = datetime.strptime(date_str, '%Y%m%d').date()
                
                if file_date < cutoff_date:
                    file_path.unlink()
                    print(f"Deleted old history file: {file_path}")
            except (ValueError, OSError) as e:
                print(f"Error processing history file {file_path}: {e}")