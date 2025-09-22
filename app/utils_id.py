# app/utils_id.py
import re
from urllib.parse import urlparse, parse_qs

HEX32_RE = re.compile(r"[0-9a-fA-F]{32}")

def _hyphenate(hex32: str) -> str:
    """32자리 hex를 Notion UUID 형태(8-4-4-4-12)로 하이픈 삽입"""
    h = hex32.lower()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"

def normalize_notion_id(s: str) -> str:
    """
    사용자가 준 입력(s)이 아래 어떤 형태든지 간에 유효한 Notion ID로 정규화:
    - 하이픈 있는 UUID: 그대로 통과
    - 하이픈 없는 32자리 hex: 하이픈 삽입
    - URL(신/구 포맷): 경로/쿼리에서 32자리 hex 추출 후 하이픈 삽입
    실패 시 ValueError
    """
    if not s:
        raise ValueError("빈 값은 유효한 Notion ID가 아닙니다.")

    s = s.strip()

    # 1) 이미 하이픈 있는 UUID인 경우(대략적인 검사)
    if re.fullmatch(r"[0-9a-fA-F\-]{36}", s) and len(s) == 36 and "-" in s:
        return s.lower()

    # 2) 하이픈 없는 32자리 hex 그대로 들어온 경우
    if HEX32_RE.fullmatch(s):
        return _hyphenate(s)

    # 3) URL에서 추출 (구/신 공유 링크 대응)
    if s.startswith("http://") or s.startswith("https://") or s.startswith("notion://"):
        u = urlparse(s)

        # (a) 경로에서 32 hex 찾기: /.../<title>-<32hex> 또는 /<32hex>
        m = HEX32_RE.search(u.path)
        if m:
            return _hyphenate(m.group(0))

        # (b) 쿼리에 p=32hex 가 담기는 경우
        q = parse_qs(u.query)
        pvals = q.get("p") or q.get("pageId") or q.get("page_id")
        if pvals:
            for val in pvals:
                if HEX32_RE.fullmatch(val):
                    return _hyphenate(val)

        # (c) 일부 링크는 shareLinkId만 들어있어 page id가 없을 수 있음 → 실패
        raise ValueError("URL에서 유효한 32자리 Page ID를 찾지 못했습니다.")

    # 그 외는 지원하지 않는 형식
    raise ValueError("유효한 Notion 페이지 ID 또는 URL이 아닙니다.")