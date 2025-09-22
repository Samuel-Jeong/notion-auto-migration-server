import re
from urllib.parse import urlparse

_32 = re.compile(r"^[0-9a-fA-F]{32}$")
_uuid = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

def _hyphenate(s32: str) -> str:
    return f"{s32[0:8]}-{s32[8:12]}-{s32[12:16]}-{s32[16:20]}-{s32[20:32]}"

def normalize_notion_id(raw: str) -> str:
    if not raw:
        return raw
    s = raw.strip()
    if _uuid.match(s):
        return s
    if _32.match(s):
        return _hyphenate(s.lower())
    # Extract from URL
    try:
        u = urlparse(s)
        tail = u.path.split("/")[-1]
        # If tail is in format abcdef1234567890abcdef1234567890, hyphenate it
        m = re.search(r"([0-9a-fA-F]{32})", tail)
        if m:
            return _hyphenate(m.group(1).lower())
    except Exception:
        pass
    return s