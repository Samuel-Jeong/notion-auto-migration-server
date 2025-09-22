from fastapi import Depends, HTTPException, status
from .config import get_settings, Settings

def require_settings(settings: Settings = Depends(get_settings)) -> Settings:
    if not settings.NOTION_TOKEN:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail="NOTION_TOKEN이 설정되지 않았습니다.")
    return settings