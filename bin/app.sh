#!/usr/bin/env bash
# bin/app.sh — Notion Local Backup (Docker/Compose One-Click Manager)
# 사용법:
#   ./bin/app.sh up            # 빌드+기동(+헬스체크 대기)
#   ./bin/app.sh build         # 이미지 빌드
#   ./bin/app.sh down          # 중지/정리
#   ./bin/app.sh restart       # 재시작
#   ./bin/app.sh logs [-f]     # 로그 보기
#   ./bin/app.sh ps            # 상태 표시
#   ./bin/app.sh sh            # 컨테이너 셸 진입
#   ./bin/app.sh health        # 헬스 상태 출력(대기 X)
#   ./bin/app.sh doctor        # 사전 점검(필수 파일/명령어)
# 옵션:
#   PORT=8080 ./bin/app.sh up  # 호스트 노출 포트 변경(기본 8000)

set -Eeuo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# 기본 설정
# ─────────────────────────────────────────────────────────────────────────────
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="notion-local-backup"
COMPOSE_FILE="${PROJECT_ROOT}/docker-compose.yml"
EXAMPLE_CFG="${PROJECT_ROOT}/deploy/config.yaml.example"
LIVE_CFG="${PROJECT_ROOT}/deploy/config.yaml"
DUMPS_DIR="${PROJECT_ROOT}/_dumps"
DEFAULT_PORT="${PORT:-8000}"
HEALTH_URL="http://localhost:${DEFAULT_PORT}/health"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-90}"   # up 시 대기 최대 초
LOG_TAIL="${LOG_TAIL:-200}"

# ─────────────────────────────────────────────────────────────────────────────
# docker compose / docker-compose 감지
# ─────────────────────────────────────────────────────────────────────────────
detect_dc() {
  if command -v docker-compose >/dev/null 2>&1; then
    echo "docker-compose"
  else
    echo "docker compose"
  fi
}
DC="$(detect_dc)"

# ─────────────────────────────────────────────────────────────────────────────
# 공통 유틸
# ─────────────────────────────────────────────────────────────────────────────
say()  { printf "[%s] %s\n" "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
err()  { printf "[%s] ERROR: %s\n" "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; }
die()  { err "$@"; exit 1; }

require_file() {
  local f="$1"
  [ -f "$f" ] || die "$f 가 없습니다."
}
ensure_dirs() {
  mkdir -p "$DUMPS_DIR"
}

ensure_config() {
  if [ ! -f "$LIVE_CFG" ]; then
    say "deploy/config.yaml 이 없어 example을 복사합니다."
    mkdir -p "$(dirname "$LIVE_CFG")"
    if [ -f "$EXAMPLE_CFG" ]; then
      cp "$EXAMPLE_CFG" "$LIVE_CFG"
      say "deploy/config.yaml 값을 편집하세요. (NOTION_TOKEN, STATIC_BASE_URL 등)"
    else
      cat >"$LIVE_CFG" <<'YAML'
# 최소 구성(필요에 맞게 수정)
NOTION_TOKEN: ""
DUMP_ROOT: "/app/_dumps"
STATIC_BASE_URL: "http://127.0.0.1:8000/files"
CRON: "0 * * * *"
AUTO_DUMP_PAGE_IDS: []
NOTION_TIMEOUT: 20
NOTION_MAX_RETRIES: 3
ASSET_MODE: "external"
ASSET_UPLOAD_MAX_MB: 20
YAML
      say "deploy/config.yaml 템플릿을 생성했습니다. 값을 채워주세요."
    fi
  fi
}

container_id() {
  # 컨테이너 ID (실행 중일 때만)
  ${DC} -f "$COMPOSE_FILE" ps -q "$SERVICE_NAME"
}

wait_health() {
  say "헬스체크 대기: ${HEALTH_URL} (timeout ${HEALTH_TIMEOUT}s)"
  local start="$(date +%s)"
  while true; do
    if curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
      say "헬스체크 OK"
      break
    fi
    sleep 2
    local now="$(date +%s)"
    if (( now - start > HEALTH_TIMEOUT )); then
      die "헬스체크 실패(시간초과). logs로 상태를 확인하세요."
    fi
  done
}

doctor() {
  command -v docker >/dev/null 2>&1 || die "docker 명령어가 필요합니다."
  ${DC} version >/dev/null 2>&1 || die "'${DC}' 명령어를 사용할 수 없습니다."
  require_file "$COMPOSE_FILE"
  say "Docker/Compose OK"
  say "compose 파일: $COMPOSE_FILE"
  say "서비스 이름: $SERVICE_NAME"
  return 0
}

print_usage() {
  cat <<EOF
사용법:
  $0 up            # 빌드+기동(+헬스체크 대기)
  $0 build         # 이미지 빌드
  $0 down          # 중지/정리
  $0 restart       # 재시작
  $0 logs [-f]     # 로그 보기
  $0 ps            # 상태 표시
  $0 sh            # 컨테이너 셸 진입
  $0 health        # 헬스 상태 출력(대기 X)
  $0 doctor        # 사전 점검
옵션:
  PORT=8080 $0 up  # 호스트 포트 변경(기본 8000)
환경:
  HEALTH_TIMEOUT(기본 90), LOG_TAIL(기본 200)
EOF
}

# ─────────────────────────────────────────────────────────────────────────────
# 명령들
# ─────────────────────────────────────────────────────────────────────────────
cmd_build() {
  doctor
  ensure_config
  ensure_dirs
  say "이미지 빌드"
  ${DC} -f "$COMPOSE_FILE" build
}

cmd_up() {
  doctor
  ensure_config
  ensure_dirs
  say "기동: 포트 ${DEFAULT_PORT} → 컨테이너 8000"
  PORT="$DEFAULT_PORT" ${DC} -f "$COMPOSE_FILE" up -d --build
  ${DC} -f "$COMPOSE_FILE" ps
  wait_health
}

cmd_down() {
  doctor
  say "중지/정리"
  ${DC} -f "$COMPOSE_FILE" down
}

cmd_restart() {
  doctor
  say "재시작"
  ${DC} -f "$COMPOSE_FILE" restart
  wait_health
}

cmd_logs() {
  doctor
  ${DC} -f "$COMPOSE_FILE" logs --tail="${LOG_TAIL}" "$@" "$SERVICE_NAME"
}

cmd_ps() {
  doctor
  ${DC} -f "$COMPOSE_FILE" ps
}

cmd_sh() {
  doctor
  local cid
  cid="$(container_id)"
  [ -n "${cid:-}" ] || die "컨테이너가 실행 중이 아닙니다. 먼저 up 하세요."
  say "컨테이너 셸 진입: $cid"
  docker exec -it "$cid" bash
}

cmd_health() {
  curl -fsS "$HEALTH_URL" || exit $?
  echo
}

# ─────────────────────────────────────────────────────────────────────────────
# 진입점
# ─────────────────────────────────────────────────────────────────────────────
main() {
  local cmd="${1:-help}"
  shift || true

  case "$cmd" in
    build)    cmd_build "$@";;
    up)       cmd_up "$@";;
    down)     cmd_down "$@";;
    restart)  cmd_restart "$@";;
    logs)     cmd_logs "$@";;
    ps)       cmd_ps "$@";;
    sh|shell) cmd_sh "$@";;
    health)   cmd_health "$@";;
    doctor)   doctor;;
    help|-h|--help) print_usage;;
    *) err "알 수 없는 명령: $cmd"; print_usage; exit 1;;
  esac
}

main "$@"