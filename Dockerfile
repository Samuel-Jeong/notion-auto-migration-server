# Dockerfile
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Seoul

# curl(healthcheck), tzdata
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 의존성
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# 앱 소스 복사
# ⬇️ 우리 템플릿은 app/templates/ 아래에 있으므로 이 한 줄이면 충분합니다.
COPY app /app/app
# 만약 리포지토리 루트에 templates/ 폴더가 따로 있다면 아래를 주석 해제하세요.
# COPY templates /app/templates

# 덤프 디렉터리 준비
RUN mkdir -p /app/_dumps

# 엔트리포인트는 루트 권한에서 복사 + 권한 부여(+x) + CRLF 제거
COPY docker/entrypoint.sh /app/entrypoint.sh
RUN sed -i 's/\r$//' /app/entrypoint.sh && chmod +x /app/entrypoint.sh

# 비루트 유저로 전환 (앱 실행)
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

ENV UVICORN_HOST=0.0.0.0 \
    UVICORN_PORT=8000 \
    UVICORN_WORKERS=1

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]