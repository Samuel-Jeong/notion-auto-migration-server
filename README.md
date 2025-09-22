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

### config.yaml
```yaml
notion_api_token: "secret_xxxxxxxxxxxxx"
static_base_url: "http://localhost:8000/files"
dump_root: "_dumps"
cron: "30 2 * * *"  # 매일 02:30

# 자동 덤프 대상 페이지들
auto_dump_page_ids:
  - "page-id-1"
  - "page-id-2"
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
```
- `config.yaml`의 `notion_api_token` 확인
- [노션 인테그레이션](https://www.notion.so/my-integrations) 페이지에서 새 토큰 생성

**2. 페이지 접근 권한 오류**
```
403 Forbidden: Access denied
```
- 인테그레이션을 해당 페이지에 초대했는지 확인
- 페이지 공유 설정에서 인테그레이션 권한 부여

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

이 프로젝트는 MIT 라이선스 하에 배포됩니다.

---

## 📞 지원

문제가 발생하거나 질문이 있으시면:
- GitHub Issues에 문제 보고
- 로그 파일 첨부 (`_logs/app.log`)
- 환경 정보 제공 (Docker/로컬, OS, Python 버전)

---