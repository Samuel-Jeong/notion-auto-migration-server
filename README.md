# Notion Local Backup & Migration

노션(Notion) 페이지를 **로컬 디렉토리에 완전히 덤프**하고,  
이미지/파일까지 포함하여 **정적 서버로 서빙**하며,  
원하는 경우 다른 노션 페이지로 **완전 재귀 마이그레이션**까지 할 수 있는 **FastAPI 기반 도구**입니다.

---

## ✨ 주요 기능

### ✅ 덤프 (Dump)
- 지정한 **노션 페이지 + 하위 블록 전체**를 로컬에 저장
- 모든 블록 구조를 `tree.json`에 기록
- 파일/이미지/동영상/PDF 등 **첨부 파일을 로컬에 다운로드**
- `manifest.json`에 블록-파일 매핑 저장

### ✅ 마이그레이션 (Migration)
- 덤프된 `tree.json`을 읽어 **다른 노션 페이지 하위에 그대로 복원**
- append 응답의 **블록 ID를 추적**하여 **재귀적으로 전부 생성**
- 첨부 파일은 로컬 정적 서버(`/files/...`) URL로 교체 → 노션에서 접근 가능

### ✅ 자동화
- `.env`에 설정한 **CRON 스케줄**에 따라 자동 덤프 실행 (예: 매일 02:30)

### ✅ 웹 UI
- `http://127.0.0.1:8000/`
- **수동 덤프 실행**, **마이그레이션 실행**, **덤프 목록 조회**
- `manifest.json` 다운로드 및 `/files/...` 정적 경로 확인 가능

### ✅ REST API
- `/api/dump` → 수동 덤프 실행
- `/api/migrate` → 특정 덤프를 타겟 페이지로 마이그레이션
- `/api/dumps` → 덤프 목록 조회
- `/docs` → Swagger 문서

---

## 📂 디렉토리 구조
~~~
notion-local-backup/
├─ app/
│  ├─ main.py              # FastAPI 진입점
│  ├─ config.py            # 설정 로더(.env)
│  ├─ deps.py              # 의존성 관리
│  ├─ notion_client.py     # Notion SDK + 재시도 래퍼
│  ├─ dump_service.py      # 덤프 서비스(트리 순회 + 파일 저장)
│  ├─ migrate_service.py   # 마이그레이션 서비스(완전 재귀)
│  ├─ routers/
│  │  ├─ api.py            # API 엔드포인트
│  │  └─ ui.py             # 웹 UI 라우터
│  ├─ templates/
│  │  └─ index.html        # 웹 UI 페이지
│  └─ static/              # (선택) UI 정적 자산
├─ _dumps/                 # 덤프 결과 저장 경로 (.env에서 지정)
├─ requirements.txt
├─ .env.example
├─ .gitignore
└─ README.md
~~~

---