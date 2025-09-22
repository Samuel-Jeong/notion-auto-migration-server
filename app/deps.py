from .config import get_settings, Settings

def require_settings() -> Settings:
    # Load settings for each request (reflects file/environment changes)
    return get_settings()