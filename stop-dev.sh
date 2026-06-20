#!/usr/bin/env bash
# =============================================================================
# 停止本地开发环境 —— 清理 start-dev.sh 起的残留进程
#
# 正常情况下 start-dev.sh 里 Ctrl+C 就会清干净；这个脚本用于：
#   - start-dev.sh 被强杀 / 终端被关，留下了孤儿进程；
#   - 端口被占（54351 / 5173 起不来），想一键清场。
#
# 用法：
#   ./stop-dev.sh                 # 停 API / worker / beat / 前端（不动 docker 依赖）
#   STOP_INFRA=1 ./stop-dev.sh    # 同时 docker compose stop redis postgres
# =============================================================================
set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

API_PORT="${API_PORT:-54351}"
WEB_PORT="${WEB_PORT:-5173}"
STOP_INFRA="${STOP_INFRA:-0}"

log()  { printf "\033[1;36m▶ %s\033[0m\n" "$*"; }

# 按端口杀（精准：只杀监听该端口的进程）
kill_port() {
  local label="$1" port="$2" pids
  pids="$(lsof -ti "tcp:$port" 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    log "停止 $label（:$port）→ PID $pids"
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null || true
    sleep 1
    pids="$(lsof -ti "tcp:$port" 2>/dev/null || true)"
    # shellcheck disable=SC2086
    [[ -n "$pids" ]] && kill -9 $pids 2>/dev/null || true
  else
    log "$label（:$port）未在运行"
  fi
}

# 按命令特征杀（用于没有端口的 celery，特征带 celery_app 足够精准）
kill_pat() {
  local label="$1" pat="$2" pids
  pids="$(pgrep -f "$pat" 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    log "停止 $label → PID $pids"
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null || true
    sleep 1
    pids="$(pgrep -f "$pat" 2>/dev/null || true)"
    # shellcheck disable=SC2086
    [[ -n "$pids" ]] && kill -9 $pids 2>/dev/null || true
  else
    log "$label 未在运行"
  fi
}

kill_port "前端 Vite" "$WEB_PORT"
kill_port "后端 API"  "$API_PORT"
kill_pat  "uvicorn"        "uvicorn server.main:app"
kill_pat  "Celery worker"  "celery -A celery_app worker"
kill_pat  "Celery beat"    "celery -A celery_app beat"

if [[ "$STOP_INFRA" == "1" ]]; then
  if command -v docker >/dev/null 2>&1; then
    log "docker compose stop redis postgres"
    docker compose stop redis postgres || true
  fi
fi

log "清理完成 ✓"
