#!/usr/bin/env bash
set -euo pipefail

mkdir -p /app/_dumps
mkdir -p /app/_logs

if [ ! -f /app/config.yaml ]; then
  echo "[WARN] /app/config.yaml 이 없습니다. 기본값으로 기동합니다. (deploy/config.yaml을 바인드하세요)"
fi

echo "[INFO] Starting uvicorn (workers=${UVICORN_WORKERS:-1}) ..."
exec python -m uvicorn app.main:app \
  --host "${UVICORN_HOST:-0.0.0.0}" \
  --port "${UVICORN_PORT:-8000}" \
  --proxy-headers \
  --timeout-keep-alive 65