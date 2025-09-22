import os
from typing import List, Dict, Any, Optional, Union

from pydantic import BaseModel, Field
try:
    import yaml  # pyyaml
except Exception:  # pragma: no cover
    yaml = None


class Settings(BaseModel):
    # Notion/server common settings
    NOTION_TOKEN: str = Field(default="", description="Notion internal integration token")
    DUMP_ROOT: str = Field(default="./_dumps")
    STATIC_BASE_URL: str = Field(default="http://127.0.0.1:8000/files")

    # Schedule (cron syntax)
    CRON: str = Field(default="0 * * * *", description="Default: every hour on the hour")

    # --- Auto dump page ID management from here ---
    # New (recommended): list format
    AUTO_DUMP_PAGE_IDS: List[str] = Field(default_factory=list)

    # Legacy (backward compatibility): single string
    AUTO_DUMP_PAGE_ID: str = Field(default="", description="DEPRECATED: use AUTO_DUMP_PAGE_IDS")

    # Notion API options
    NOTION_TIMEOUT: int = Field(default=15)
    NOTION_MAX_RETRIES: int = Field(default=3)

    # Recommended list usage: effective value (merge new/legacy versions)
    @property
    def AUTO_DUMP_PAGE_IDS_EFFECTIVE(self) -> List[str]:
        return self.auto_dump_ids()

    def auto_dump_ids(self) -> List[str]:
        """
        Merge AUTO_DUMP_PAGE_IDS (list) and AUTO_DUMP_PAGE_ID (single) with
        space/comma separation support, removing empty values, duplicates while preserving order.
        """
        merged: List[str] = []

        def push(v: Optional[str]):
            if not v:
                return
            s = v.strip()
            if not s:
                return
            if s not in merged:
                merged.append(s)

        # 1) List first
        for item in self.AUTO_DUMP_PAGE_IDS or []:
            # Allow splitting if item is comma-separated string like "a,b"
            if isinstance(item, str) and ("," in item or " " in item):
                for token in _split_maybe_list_string(item):
                    push(token)
            else:
                push(str(item))

        # 2) Merge legacy single value
        for token in _split_maybe_list_string(self.AUTO_DUMP_PAGE_ID):
            push(token)

        return merged


def _split_maybe_list_string(v: Optional[str]) -> List[str]:
    """
    Convert comma/space-separated string to list.
    Example: "aaa,bbb  ccc" -> ["aaa","bbb","ccc"]
    """
    if not v:
        return []
    raw = v.strip()
    if not raw:
        return []
    parts: List[str] = []
    for seg in raw.replace("\n", " ").split(","):
        seg = seg.strip()
        if not seg:
            continue
        parts.extend([p for p in seg.split() if p])
    return parts


def _read_yaml_config(path: str) -> Dict[str, Any]:
    if not path or not os.path.exists(path):
        return {}
    if yaml is None:
        raise RuntimeError("pyyaml is not installed. Please add pyyaml to requirements.")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            raise ValueError("config.yaml top level must be a mapping (dict).")
        return data


def _coerce_auto_dump_ids(src: Union[None, str, List[Any]]) -> List[str]:
    """
    Safely normalize various forms that can come into AUTO_DUMP_PAGE_IDS to list.
    - None -> []
    - "a,b c" -> ["a","b","c"]
    - ["a", "b c", "d,e"] -> ["a","b","c","d","e"]
    """
    out: List[str] = []
    if src is None:
        return out
    if isinstance(src, str):
        return _split_maybe_list_string(src)
    if isinstance(src, list):
        for item in src:
            if isinstance(item, str):
                tokens = _split_maybe_list_string(item)
                if tokens:
                    out.extend(tokens)
            elif item is not None:
                out.append(str(item))
        return [s for s in out if s]
    # 기타 타입은 문자열로 강제
    return [str(src)]


def get_settings(config_path: Optional[str] = None) -> Settings:
    """
    우선순위
    1) YAML 파일(config.yaml 또는 지정 경로)
    2) 환경변수
    - 리스트 키는 콤마/공백 구분 문자열도 허용
    - 단일/리스트 키를 병행해서 제공하면 합쳐짐
    """
    # 1) YAML 읽기
    if config_path is None:
        # 프로젝트 루트에 기본 config.yaml이 있다고 가정
        default_path = os.environ.get("CONFIG_PATH", "./config.yaml")
    else:
        default_path = config_path

    y = _read_yaml_config(default_path)

    # YAML에서 값 꺼내기(없으면 None)
    y_token = y.get("NOTION_TOKEN")
    y_dump_root = y.get("DUMP_ROOT")
    y_static = y.get("STATIC_BASE_URL")
    y_cron = y.get("CRON")

    # 신규 리스트 키/구버전 단일 키
    y_ids_list = _coerce_auto_dump_ids(y.get("AUTO_DUMP_PAGE_IDS"))
    y_id_single = y.get("AUTO_DUMP_PAGE_ID")

    y_timeout = y.get("NOTION_TIMEOUT")
    y_retries = y.get("NOTION_MAX_RETRIES")

    # 2) 환경변수(없으면 YAML 값 사용)
    env = os.environ
    token = env.get("NOTION_TOKEN", y_token or "")
    dump_root = env.get("DUMP_ROOT", y_dump_root or "./_dumps")
    static_base = env.get("STATIC_BASE_URL", y_static or "http://127.0.0.1:8000/files")
    cron = env.get("CRON", y_cron or "0 * * * *")

    # 리스트/단일 혼합 수용
    env_ids_list = _coerce_auto_dump_ids(env.get("AUTO_DUMP_PAGE_IDS"))
    env_id_single = env.get("AUTO_DUMP_PAGE_ID", y_id_single or "")

    # 최종 리스트는 리스트(ENV→YAML) + 단일(ENV→YAML) 병합
    # (Settings 내부에서 다시 병합하지만, 기본값 채움 차원에서 먼저 생성)
    final_ids_list: List[str] = []
    seen = set()

    def add_one(v: Optional[str]):
        if not v:
            return
        s = v.strip()
        if not s or s in seen:
            return
        seen.add(s)
        final_ids_list.append(s)

    for v in env_ids_list or y_ids_list:
        add_one(v)

    for v in _split_maybe_list_string(env_id_single):
        add_one(v)

    # 숫자 파싱(환경변수 우선)
    try:
        timeout = int(env.get("NOTION_TIMEOUT", y_timeout if y_timeout is not None else 15))
    except Exception:
        timeout = 15
    try:
        retries = int(env.get("NOTION_MAX_RETRIES", y_retries if y_retries is not None else 3))
    except Exception:
        retries = 3

    settings = Settings(
        NOTION_TOKEN=token,
        DUMP_ROOT=dump_root,
        STATIC_BASE_URL=static_base,
        CRON=cron,
        AUTO_DUMP_PAGE_IDS=final_ids_list,
        AUTO_DUMP_PAGE_ID=env_id_single or (y_id_single or ""),
        NOTION_TIMEOUT=timeout,
        NOTION_MAX_RETRIES=retries,
    )
    return settings