# Notion Auto Migration Server

노션(Notion) 페이지를 **로컬 디렉토리에 완전히 덤프**하고,  
이미지/파일까지 포함하여 **정적 서버로 서빙**하며,  
원하는 경우 다른 노션 페이지로 **완전 재귀 마이그레이션**까지 할 수 있는 **FastAPI 기반 도구**입니다.

**v1.2.0** - 작업 관리자, 실시간 모니터링, Docker 지원 추가

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

### ✅ 작업 관리 시스템
- **JobManager**를 통한 동시 작업 제한 (덤프 최대 3개, 마이그레이션 최대 3개)
- 작업 상태 추적 (대기, 진행중, 완료, 실패, 취소)
- 작업 취소 및 삭제 기능
- **실시간 작업 모니터링** (Server-Sent Events)

### ✅ 자동화
- **CRON 스케줄**에 따라 자동 덤프 실행 (예: 매일 02:30)
- Asia/Seoul 타임존 지원
- 스케줄러 큐를 통한 안전한 비동기 처리

### ✅ 웹 UI
- `http://127.0.0.1:8000/`
- **수동 덤프 실행**, **마이그레이션 실행**, **덤프 목록 조회**
- **실시간 작업 상태 모니터링**
- `manifest.json` 다운로드 및 `/files/...` 정적 경로 확인 가능
- **파일 브라우저** (`/api/browse/{dump_name}/`)

### ✅ REST API
- **덤프 관리**
  - `POST /api/dump` → 수동 덤프 실행
  - `GET /api/dumps` → 덤프 목록 조회
  - `GET /api/dumps/stream` → 실시간 덤프 목록 스트림
  - `DELETE /api/dump/{name}` → 덤프 삭제
- **마이그레이션**
  - `POST /api/migrate` → 특정 덤프를 타겟 페이지로 마이그레이션
- **작업 관리**
  - `GET /jobs` → 작업 목록 조회
  - `POST /jobs/dump` → 덤프 작업 생성
  - `POST /jobs/migrate` → 마이그레이션 작업 생성
  - `POST /jobs/{job_id}/cancel` → 작업 취소
  - `POST /jobs/{job_id}/remove` → 작업 삭제
  - `GET /jobs/stream` → 실시간 작업 상태 스트림 (SSE)
- **시스템**
  - `GET /health` → 헬스 체크
  - `GET /docs` → Swagger 문서

### ✅ 고급 기능
- **로그 관리**: 순환 로그 파일 (app.log, uvicorn.log, access.log)
- **파일 브라우저**: 웹에서 덤프된 파일 탐색
- **보안**: 경로 순회 공격 방지, 비루트 사용자로 실행
- **Docker 지원**: 프로덕션 환경 배포 지원

---

## 🚀 빠른 시작

### Docker로 실행 (권장)

1. **Docker Compose 사용**
```bash
# 리포지토리 클론
git clone <repository-url>
cd notion-auto-migration-server

# 설정 파일 준비
cp deploy/config.yaml.example deploy/config.yaml
# deploy/config.yaml 에서 노션 API 설정

# 컨테이너 실행
docker-compose up -d

# 로그 확인
docker-compose logs -f
```

2. **직접 Docker 실행**
```bash
# 이미지 빌드
docker build -t notion-auto-migration-server .

# 컨테이너 실행
docker run -d \
  --name notion-backup \
  -p 8000:8000 \
  -v $(pwd)/_dumps:/app/_dumps \
  -v $(pwd)/_logs:/app/_logs \
  -v $(pwd)/deploy/config.yaml:/app/config.yaml:ro \
  notion-auto-migration-server
```

### 로컬 개발 환경

```bash
# 의존성 설치
pip install -r requirements.txt

# 설정 파일 준비
cp config.yaml.example config.yaml
# config.yaml 에서 노션 API 설정

# 개발 서버 실행
chmod +x bin/app.sh
./bin/app.sh

# 또는 직접 실행
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

---

## ⚙️ 설정

### 🔑 Notion API 토큰 설정 (필수)

#### 1. Notion Integration 생성
1. [Notion 인테그레이션 페이지](https://www.notion.so/my-integrations)에 접속
2. **"New integration"** 클릭
3. **Integration 정보 입력:**
   - **Name**: `notion-auto-migration` (원하는 이름)
   - **Logo**: 선택사항
   - **Associated workspace**: 사용할 워크스페이스 선택
4. **"Submit"** 클릭하여 인테그레이션 생성
5. **"Internal Integration Token"** 복사 (예: `secret_ABC123...` 또는 `ntn_ABC123...`)

#### 2. 페이지 권한 부여
생성된 인테그레이션을 사용할 노션 페이지에 권한을 부여해야 합니다:

1. **노션 페이지 열기** (덤프하려는 페이지)
2. 페이지 우상단 **"..."** (더보기) 메뉴 클릭
3. **"Add connections"** 또는 **"연결 추가"** 클릭
4. 앞서 생성한 인테그레이션 선택 (예: `notion-auto-migration`)
5. **"Confirm"** 또는 **"확인"** 클릭

⚠️ **중요**: 각 덤프하려는 페이지마다 이 권한 부여 과정을 반복해야 합니다!

### 📄 Notion 페이지 ID 찾기

#### 방법 1: 브라우저 URL에서 추출 (권장)
```
https://www.notion.so/workspace/Page-Title-32자리ID?pvs=4
                                    ^^^^^^^^^^^^^^^^
                                    이 32자리가 페이지 ID
```

**예시:**
```
URL: https://www.notion.so/jamesj/My-Project-6df9171ff9ce4041a3a6e814a120c92d?pvs=4
페이지 ID: 6df9171ff9ce4041a3a6e814a120c92d
```

#### 방법 2: 페이지 공유 링크 사용
1. 노션 페이지에서 **"Share"** 또는 **"공유"** 버튼 클릭
2. **"Copy link"** 또는 **"링크 복사"** 클릭
3. 복사된 링크에서 32자리 ID 추출 또는 **전체 URL 사용 가능**

**두 가지 모두 지원:**
```yaml
# 페이지 ID만 사용
AUTO_DUMP_PAGE_IDS:
  - "6df9171ff9ce4041a3a6e814a120c92d"

# 전체 URL 사용 (자동으로 ID 추출됨)
AUTO_DUMP_PAGE_IDS:
  - "https://www.notion.so/workspace/Page-Title-6df9171ff9ce4041a3a6e814a120c92d?pvs=4"
```

### 📋 config.yaml 설정

#### 기본 설정 파일
```yaml
# 🔑 필수: Notion API 토큰
NOTION_TOKEN: "secret_your_integration_token_here"

# 🌐 서버 설정
STATIC_BASE_URL: "http://localhost:8000/files"  # 정적 파일 서빙 URL
DUMP_ROOT: "./_dumps"                           # 덤프 저장 디렉토리

# ⏰ 자동 덤프 스케줄 (CRON 형식)
CRON: "30 2 * * *"  # 매일 02:30 (Asia/Seoul 기준)

# 📄 자동 덤프 대상 페이지들 (권장: 리스트 형식)
AUTO_DUMP_PAGE_IDS:
  - "6df9171ff9ce4041a3a6e814a120c92d"  # 페이지 ID만
  - "https://www.notion.so/workspace/Another-Page-abc123def456?pvs=4"  # 전체 URL

# 🔧 API 설정 (선택사항)
NOTION_TIMEOUT: 15      # API 타임아웃 (초)
NOTION_MAX_RETRIES: 3   # API 재시도 횟수

# 📁 파일 업로드 설정 (선택사항)
ASSET_MODE: "upload"           # upload 또는 link
ASSET_UPLOAD_MAX_MB: 100       # 최대 업로드 파일 크기 (MB)
```

#### 고급 설정 예시

**1. 여러 페이지 자동 덤프**
```yaml
AUTO_DUMP_PAGE_IDS:
  - "page-id-1"
  - "page-id-2"
  - "page-id-3"
  - "https://www.notion.so/workspace/Full-URL-page-id-4"
```

**2. 다양한 페이지 ID 형식 (모두 지원)**
```yaml
AUTO_DUMP_PAGE_IDS:
  - "6df9171ff9ce4041a3a6e814a120c92d"                                    # 순수 ID
  - "6df9171f-f9ce-4041-a3a6-e814a120c92d"                                # 하이픈 포함
  - "https://www.notion.so/workspace/Page-Title-6df9171ff9ce4041a3a6e814a120c92d"  # 전체 URL
  - "https://www.notion.so/6df9171ff9ce4041a3a6e814a120c92d?pvs=4"         # 파라미터 포함
```

**3. 콤마/공백 구분 문자열 (하나의 문자열로 여러 ID)**
```yaml
AUTO_DUMP_PAGE_IDS:
  - "page-id-1, page-id-2 page-id-3"  # 콤마와 공백으로 구분
```

**4. 프로덕션 환경 설정**
```yaml
NOTION_TOKEN: "secret_production_token"
STATIC_BASE_URL: "https://your-domain.com/files"
DUMP_ROOT: "/data/dumps"
CRON: "0 3 * * *"                      # 매일 03:00 실행
LOG_LEVEL: "WARNING"                   # 로그 레벨 조정
NOTION_TIMEOUT: 30                     # 프로덕션에서는 더 긴 타임아웃
NOTION_MAX_RETRIES: 5                  # 더 많은 재시도
```

### 환경 변수
```bash
# 로그 설정
LOG_DIR=/app/_logs
LOG_LEVEL=INFO

# 서버 설정
UVICORN_HOST=0.0.0.0
UVICORN_PORT=8000
UVICORN_WORKERS=1

# 타임존
TZ=Asia/Seoul
```

### 🚀 빠른 설정 체크리스트

설정 과정을 단계별로 확인하세요:

**1단계: Notion Integration 준비 ✅**
- [ ] [Notion 인테그레이션 페이지](https://www.notion.so/my-integrations) 접속
- [ ] "New integration" 생성 완료
- [ ] Internal Integration Token 복사 (`secret_...` 또는 `ntn_...`)

**2단계: 페이지 권한 설정 ✅**  
- [ ] 덤프할 노션 페이지 열기
- [ ] 페이지 우상단 `...` → `Add connections` 클릭
- [ ] 생성한 인테그레이션 선택 및 권한 부여
- [ ] 모든 덤프 대상 페이지에 반복 적용

**3단계: 페이지 ID 수집 ✅**
- [ ] 브라우저 URL에서 32자리 페이지 ID 추출
- [ ] 또는 전체 공유 링크 복사 (자동 추출 지원)

**4단계: config.yaml 설정 ✅**
```yaml
NOTION_TOKEN: "여기에_복사한_토큰_붙여넣기"
AUTO_DUMP_PAGE_IDS:
  - "페이지ID1"
  - "페이지ID2"  # 필요한 만큼 추가
```

**5단계: 테스트 실행 ✅**
```bash
# 서버 시작
./bin/app.sh  # 또는 docker-compose up

# 브라우저에서 확인
http://localhost:8000
```

⚠️ **설정 실패 시**: 아래 트러블슈팅 섹션 참조

---

## 📂 프로젝트 구조
```
notion-auto-migration-server/
├── app/
│   ├── main.py              # FastAPI 진입점
│   ├── config.py            # 설정 로더
│   ├── deps.py              # 의존성 관리
│   ├── jobs.py              # 작업 관리자
│   ├── notion_client.py     # Notion SDK 래퍼
│   ├── dump_service.py      # 덤프 서비스
│   ├── migrate_service.py   # 마이그레이션 서비스
│   ├── utils_id.py          # ID 정규화 유틸
│   ├── routers/
│   │   ├── api.py           # REST API 엔드포인트
│   │   ├── ui.py            # 웹 UI 라우터
│   │   └── jobs.py          # 작업 관리 API
│   └── templates/
│       └── index.html       # 웹 UI 페이지
├── docker/
│   └── entrypoint.sh        # Docker 엔트리포인트
├── deploy/
│   └── config.yaml          # 배포용 설정
├── bin/
│   └── app.sh              # 실행 스크립트
├── _dumps/                  # 덤프 저장소
├── _logs/                   # 로그 파일들
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── README.md
```

---

## 📖 사용법

### 1. 웹 UI 사용
1. 브라우저에서 `http://localhost:8000` 접속
2. **덤프 실행**: 노션 페이지 ID 입력 후 "덤프 시작" 클릭
3. **마이그레이션 실행**: 덤프명과 대상 페이지 ID 입력 후 실행
4. **실시간 모니터링**: 작업 진행 상황을 실시간으로 확인
5. **파일 탐색**: 덤프된 파일을 웹 브라우저에서 탐색

### 2. API 사용 예제

**덤프 작업 생성**
```bash
curl -X POST "http://localhost:8000/jobs/dump" \
  -H "Content-Type: application/json" \
  -d '{"page_id": "your-notion-page-id"}'
```

**마이그레이션 작업 생성**
```bash
curl -X POST "http://localhost:8000/jobs/migrate" \
  -H "Content-Type: application/json" \
  -d '{"dump_name": "dump_name", "target_page_id": "target-page-id"}'
```

**작업 목록 조회**
```bash
curl "http://localhost:8000/jobs"
```

**실시간 작업 모니터링 (Server-Sent Events)**
```bash
curl -N "http://localhost:8000/jobs/stream"
```

### 3. 노션 페이지 ID 찾기
- 노션 페이지 URL에서 추출: `https://www.notion.so/Your-Page-Title-{PAGE_ID}`
- 페이지 공유 링크의 마지막 부분 32자리 문자열

---

## 🔧 트러블슈팅

### 자주 발생하는 문제

**1. 노션 API 토큰 오류**
```
401 Unauthorized: Invalid token
HTTPError: 401 Client Error: Unauthorized for url: https://api.notion.com/v1/...
```
**해결 방법:**
- `config.yaml`의 `NOTION_TOKEN` 값이 올바른지 확인
- 토큰이 `secret_` 또는 `ntn_`으로 시작하는지 확인
- [노션 인테그레이션 페이지](https://www.notion.so/my-integrations)에서 토큰 재생성
- 토큰 복사 시 앞뒤 공백이 없는지 확인
- 따옴표 안에 토큰을 올바르게 넣었는지 확인: `"secret_abc123..."`

**2. 페이지 접근 권한 오류**
```
403 Forbidden: Access denied
HTTPError: 403 Client Error: Forbidden for url: https://api.notion.com/v1/blocks/...
```
**해결 방법:**
- 인테그레이션을 해당 페이지에 **반드시 초대**했는지 확인
- 페이지 우상단 `...` → `Add connections` → 인테그레이션 선택
- 부모 페이지뿐만 아니라 **모든 하위 페이지**에도 권한이 필요한 경우 각각 초대
- 워크스페이스 관리자 권한이 있는 계정으로 로그인했는지 확인

**3. 페이지 ID 형식 오류**
```
400 Bad Request: Invalid page ID
ValidationError: page_id must be a valid UUID
```
**해결 방법:**
- 페이지 ID가 32자리 16진수인지 확인 (예: `6df9171ff9ce4041a3a6e814a120c92d`)
- URL에서 올바르게 추출했는지 확인:
  ```
  ❌ 잘못된 예: "My-Project-6df9171ff9ce4041a3a6e814a120c92d"
  ✅ 올바른 예: "6df9171ff9ce4041a3a6e814a120c92d"
  ```
- 전체 URL을 사용하는 경우 시스템이 자동으로 ID를 추출하므로 문제없음

**4. 페이지를 찾을 수 없음**
```
404 Not Found: Page not found
```
**해결 방법:**
- 페이지가 실제로 존재하는지 브라우저에서 확인
- 페이지가 삭제되거나 이동되지 않았는지 확인  
- 정확한 페이지 URL/ID를 사용하고 있는지 재확인
- 다른 워크스페이스의 페이지는 아닌지 확인

**5. 인테그레이션 토큰 타입 오류**
```
Error: This endpoint requires an integration token
```
**해결 방법:**
- **Internal Integration Token**을 사용해야 함 (User Token 아님)
- [노션 인테그레이션 페이지](https://www.notion.so/my-integrations)에서 생성한 토큰 사용
- `secret_`으로 시작하는 Internal Integration Token 확인

**6. YAML 설정 파일 오류**
```
yaml.parser.ParserError: while parsing...
ValueError: config.yaml top level must be a mapping (dict)
```
**해결 방법:**
- YAML 문법이 올바른지 확인 (들여쓰기, 콜론, 따옴표)
- 온라인 YAML 검증기로 문법 검사
- 예시 설정 파일과 비교하여 구조 확인:
  ```yaml
  NOTION_TOKEN: "your_token_here"  # 따옴표 필수
  AUTO_DUMP_PAGE_IDS:              # 콜론 후 공백
    - "page-id-1"                  # 리스트 항목 앞에 하이픈과 공백
    - "page-id-2"
  ```

**3. 파일 다운로드 실패**
- 네트워크 연결 상태 확인
- 노션 서버의 임시적인 문제일 수 있음 (재시도 로직 있음)

**4. Docker 컨테이너 시작 실패**
- 포트 8000이 이미 사용 중인지 확인
- `docker-compose.yml`의 포트 설정 변경: `"8001:8000"`

### 로그 확인

**Docker 환경**
```bash
# 애플리케이션 로그
docker-compose exec notion-local-backup tail -f /app/_logs/app.log

# 모든 로그
docker-compose logs -f
```

**로컬 개발**
```bash
# 로그 디렉토리 확인
ls -la _logs/

# 실시간 로그 모니터링
tail -f _logs/app.log
```

---

## 🚀 배포

### 프로덕션 환경

1. **환경 변수 설정**
```bash
export PORT=8000
export LOG_LEVEL=WARNING
export TZ=Asia/Seoul
```

2. **리버스 프록시 설정** (nginx 예시)
```nginx
server {
    listen 80;
    server_name your-domain.com;
    
    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        
        # SSE 지원
        proxy_buffering off;
        proxy_cache off;
    }
}
```

3. **백업 스크립트**
```bash
#!/bin/bash
# _dumps 디렉토리 백업
tar -czf "backup-$(date +%Y%m%d).tar.gz" _dumps/
```

---

## 🤝 기여

1. Fork the repository
2. Create a feature branch: `git checkout -b feature-name`
3. Commit your changes: `git commit -am 'Add feature'`
4. Push to the branch: `git push origin feature-name`
5. Submit a pull request

---

## 📄 라이선스

이 프로젝트는 CC BY-NC 4.0 International 라이선스 하에 배포됩니다.

---

## 📞 지원

문제가 발생하거나 질문이 있으시면:
- GitHub Issues에 문제 보고
- 로그 파일 첨부 (`_logs/app.log`)
- 환경 정보 제공 (Docker/로컬, OS, Python 버전)

---