import yaml
from pydantic import BaseModel

class Settings(BaseModel):
    NOTION_TOKEN: str
    DUMP_ROOT: str = "./_dumps"
    STATIC_BASE_URL: str = "http://127.0.0.1:8000/files"
    CRON: str = "30 2 * * *"
    AUTO_DUMP_PAGE_ID: str = ""
    NOTION_TIMEOUT: int = 15
    NOTION_MAX_RETRIES: int = 3

def get_settings(config_path: str = "config.yaml") -> Settings:
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return Settings(**data)