#!/usr/bin/env bash
# =============================================================================
# 本地一键启动脚本 —— 测试自动化平台开发环境
#
# 起的东西（默认全开，可用环境变量按需关）：
#   1. redis + postgres        （docker compose 起依赖，代码连 127.0.0.1）
#   2. alembic upgrade head    （建表/迁移）
#   3. FastAPI (uvicorn)       → http://127.0.0.1:54351   --reload 热更新
#   4. Celery worker           （跑用例 / AI 任务 / 设备探活）
#   5. Celery beat             （定时任务调度，一个集群只能一个）
#   6. 前端 Vite dev           → http://localhost:5173    （API 代理到 54351）
#
# 用法：
#   ./start-dev.sh                      # 全开
#   START_INFRA=0 ./start-dev.sh        # 已自己起了 redis/postgres，跳过 docker
#   START_WEB=0 ./start-dev.sh          # 只起后端
#   START_WORKER=0 START_BEAT=0 ./start-dev.sh   # 只起 API + 前端（不跑异步任务）
#   API_RELOAD=0 ./start-dev.sh         # 关掉 uvicorn 热重载
#
# Ctrl+C 一次，干净停掉所有起的进程。日志在 data/logs/dev/ 下。
# =============================================================================
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

# ---- 可调参数（环境变量覆盖）----
API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-54351}"
WEB_PORT="${WEB_PORT:-5173}"
START_INFRA="${START_INFRA:-1}"        # 用 docker compose 起 redis + postgres
RUN_MIGRATIONS="${RUN_MIGRATIONS:-1}"  # 启动前跑 alembic upgrade head
START_API="${START_API:-1}"
START_WORKER="${START_WORKER:-1}"
START_BEAT="${START_BEAT:-1}"
START_WEB="${START_WEB:-1}"
API_RELOAD="${API_RELOAD:-1}"          # uvicorn --reload

LOG_DIR="$PROJECT_ROOT/data/logs/dev"
mkdir -p "$LOG_DIR"

PIDS=()

log()  { printf "\033[1;36m▶ %s\033[0m\n" "$*"; }
warn() { printf "\033[1;33m! %s\033[0m\n" "$*"; }

cleanup() {
  # 没起任何进程（比如预检失败提前退出）就静默返回，别喊“正在停止”
  [[ "${#PIDS[@]}" -eq 0 ]] && return 0
  printf "\n"
  log "正在停止本地服务..."
  for pid in "${PIDS[@]:-}"; do
    [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null && kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
  log "已全部停止 ✓"
}
trap cleanup INT TERM EXIT

# ---- 1. Python 环境 ----
USE_VENV="${USE_VENV:-1}"
if [[ "$USE_VENV" == "1" && -f "venv/bin/activate" ]]; then
  log "激活 venv"
  # shellcheck disable=SC1091
  source venv/bin/activate
fi
PYBIN="${PYTHON:-$(command -v python || command -v python3 || true)}"
if [[ -z "$PYBIN" ]]; then
  warn "找不到 python，请安装 Python 或设 PYTHON=/path/to/python 后重试"
  exit 1
fi
log "python: $PYBIN ($("$PYBIN" --version 2>&1))"

# ---- 1.5 依赖预检 ----（缺依赖时明确提示，而不是莫名 command not found）
if ! "$PYBIN" -c "import alembic, uvicorn, celery, fastapi" >/dev/null 2>&1; then
  warn "当前 python 环境缺少后端依赖（alembic / uvicorn / celery / fastapi 等）。"
  if [[ "${AUTO_INSTALL:-0}" == "1" ]]; then
    log "AUTO_INSTALL=1 → $PYBIN -m pip install -r requirements.txt"
    "$PYBIN" -m pip install -r requirements.txt
  else
    echo "    请先安装依赖到当前/venv 环境："
    echo "        $PYBIN -m pip install -r requirements.txt"
    echo "    或让脚本自动装：  AUTO_INSTALL=1 ./start-dev.sh"
    exit 1
  fi
fi

# ---- 2. 基础设施：redis + postgres ----
if [[ "$START_INFRA" == "1" ]]; then
  if command -v docker >/dev/null 2>&1; then
    log "启动 redis + postgres（docker compose）"
    docker compose up -d redis postgres
    log "等待 postgres 就绪..."
    for _ in $(seq 1 30); do
      if docker compose exec -T postgres pg_isready -U tap >/dev/null 2>&1; then
        log "postgres 就绪 ✓"; break
      fi
      sleep 1
    done
  else
    warn "未检测到 docker，跳过基础设施（假设 redis:6379 / postgres:5432 已在运行）"
  fi
fi

# ---- 3. 数据库迁移 ----
if [[ "$RUN_MIGRATIONS" == "1" ]]; then
  log "alembic upgrade head"
  "$PYBIN" -m alembic upgrade head
fi

# ---- 4. 后端 API ----
if [[ "$START_API" == "1" ]]; then
  reload_flag=""
  [[ "$API_RELOAD" == "1" ]] && reload_flag="--reload"
  log "API (uvicorn) → http://$API_HOST:$API_PORT   日志 $LOG_DIR/api.log"
  # shellcheck disable=SC2086
  "$PYBIN" -m uvicorn server.main:app --host "$API_HOST" --port "$API_PORT" $reload_flag \
    > "$LOG_DIR/api.log" 2>&1 &
  PIDS+=($!)
fi

# ---- 5. Celery worker ----
if [[ "$START_WORKER" == "1" ]]; then
  log "Celery worker   日志 $LOG_DIR/worker.log"
  "$PYBIN" -m celery -A celery_app worker --loglevel=INFO \
    > "$LOG_DIR/worker.log" 2>&1 &
  PIDS+=($!)
fi

# ---- 6. Celery beat ----
if [[ "$START_BEAT" == "1" ]]; then
  log "Celery beat     日志 $LOG_DIR/beat.log"
  "$PYBIN" -m celery -A celery_app beat --loglevel=INFO \
    --schedule "$PROJECT_ROOT/data/celerybeat-schedule.db" \
    > "$LOG_DIR/beat.log" 2>&1 &
  PIDS+=($!)
fi

# ---- 7. 前端 dev ----
if [[ "$START_WEB" == "1" ]]; then
  if [[ -d frontend/node_modules ]]; then
    log "前端 Vite dev → http://localhost:$WEB_PORT   日志 $LOG_DIR/web.log"
    ( cd frontend && npm run dev -- --port "$WEB_PORT" ) > "$LOG_DIR/web.log" 2>&1 &
    PIDS+=($!)
  else
    warn "frontend/node_modules 不存在 —— 先 cd frontend && npm install，本次跳过前端"
  fi
fi

printf "\n"
log "全部启动完成。Ctrl+C 停止全部。"
echo "    API   : http://$API_HOST:$API_PORT"
echo "    前端  : http://localhost:$WEB_PORT  （/api 代理到后端）"
echo "    日志  : $LOG_DIR/"
printf "\n"
log "实时日志（tail -F，Ctrl+C 退出并停服）："

# 跟随所有日志，直到 Ctrl+C
tail -n +1 -F "$LOG_DIR"/*.log &
PIDS+=($!)
wait
