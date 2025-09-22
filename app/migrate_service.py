from typing import Dict, Any, List, Optional, Tuple
from fastapi.concurrency import run_in_threadpool
from .notion_client import build_client, notion_retry
from .config import Settings

# Notion 제약: children.append 한 번에 최대 100개
APPEND_LIMIT = 100

class NotionMigrateService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = build_client(settings.NOTION_TOKEN, settings.NOTION_TIMEOUT)

    @notion_retry()
    def _append_children(self, parent_block_id: str, children: List[Dict[str, Any]]) -> Dict[str, Any]:
        return self.client.blocks.children.append(block_id=parent_block_id, children=children)

    # -------------------------------
    # 에셋(URL) 치환
    # -------------------------------
    def _rewrite_file_block(
        self,
        src_node: Dict[str, Any],
        payload_block: Dict[str, Any],
        asset_url_map: Dict[str, List[str]]
    ) -> Dict[str, Any]:
        """
        덤프 시 기록한 manifest의 file path를 STATIC_BASE_URL로 노출하여 external 링크로 교체.
        같은 노드에 여러 파일이 있을 수 있으므로 첫 번째를 사용(필요 시 확장).
        """
        t = payload_block["type"]
        if t not in ("image", "file", "pdf", "video", "audio"):
            return payload_block

        node_id = src_node.get("id")
        candidates = asset_url_map.get(node_id, [])
        if not candidates:
            # 매니페스트에 파일 기록이 없다면 기존 값 유지(원 URL 그대로)
            return payload_block

        # external로 강제 교체
        data = payload_block.get(t, {})
        data["external"] = {"url": candidates[0]}
        data.pop("file", None)
        payload_block[t] = data
        return payload_block

    # -------------------------------
    # 페이로드 변환
    # -------------------------------
    def _node_to_block_payload(
        self,
        src_node: Dict[str, Any],
        asset_url_map: Dict[str, List[str]]
    ) -> Dict[str, Any]:
        """
        tree.json의 노드를 Notion children.append에 넣을 수 있는 1개 block payload로 변환.
        기본적으로 원본 노드의 type 서브페이로드를 그대로 사용하되,
        파일/이미지 계열은 external URL로 치환한다.
        """
        t = src_node["type"]
        payload = {
            "object": "block",
            "type": t,
            t: src_node.get(t, {}) or {}
        }
        # 파일/이미지 블록 external URL 치환
        payload = self._rewrite_file_block(src_node, payload, asset_url_map)
        return payload

    # -------------------------------
    # 재귀 생성의 핵심
    # -------------------------------
    async def _append_children_recursive(
        self,
        parent_id: str,
        src_children: List[Dict[str, Any]],
        asset_url_map: Dict[str, List[str]]
    ):
        """
        1) 자식 노드들을 100개 단위로 append
        2) append 응답의 results 순서가 요청 순서와 동일하므로,
           각 응답 결과 블록 ID와 src_children의 동일 인덱스를 매칭
        3) 자식에게 또 children이 있으면 방금 생성된 해당 블록 ID를 parent로 재귀
        """
        if not src_children:
            return

        # 100개 단위로 청크
        for i in range(0, len(src_children), APPEND_LIMIT):
            chunk = src_children[i:i+APPEND_LIMIT]

            # 1) 요청 페이로드로 변환
            payload_chunk: List[Dict[str, Any]] = []
            for node in chunk:
                payload_chunk.append(self._node_to_block_payload(node, asset_url_map))

            # 2) append 호출
            try:
                resp = await run_in_threadpool(self._append_children, parent_id, payload_chunk)
                results: List[Dict[str, Any]] = resp.get("results", [])
            except Exception as e:
                # 청크 전체 실패 시, 단일 재시도(격리) 전략: 각 항목을 개별 append로 시도
                # (큰 문서에서도 최대한 진행되도록 함)
                results = []
                for j, single_node in enumerate(chunk):
                    try:
                        r = await run_in_threadpool(self._append_children, parent_id, [self._node_to_block_payload(single_node, asset_url_map)])
                        if r.get("results"):
                            results.append(r["results"][0])
                        else:
                            results.append({})
                    except Exception:
                        results.append({})  # 실패 항목은 빈 dict로 채움

            # 3) 응답과 요청을 인덱스로 매칭하여 재귀
            for idx, src_node in enumerate(chunk):
                created = results[idx] if idx < len(results) else {}
                created_id = created.get("id")
                # 자식이 있으면 재귀
                if src_node.get("has_children") and src_node.get("children"):
                    # created_id가 없으면(실패) 상위 ID로라도 도전(안전장치)
                    next_parent = created_id or parent_id
                    await self._append_children_recursive(next_parent, src_node["children"], asset_url_map)

    async def migrate_under(
        self,
        target_page_id: str,
        tree: Dict[str, Any],
        asset_url_map: Optional[Dict[str, List[str]]] = None
    ):
        """
        완전 재귀 마이그레이션:
        - tree의 최상위 children을 target_page_id 하위에 append
        - 응답으로 받은 각 블록 ID를 parent로, 그 블록의 children을 또 append
        - 이를 깊이 끝까지 반복
        """
        asset_url_map = asset_url_map or {}
        children = tree.get("children", [])
        await self._append_children_recursive(target_page_id, children, asset_url_map)