#!/usr/bin/env bash
# =============================================================================
# 本地一键启动脚本 —— 测试自动化平台开发环境
#
# 起的东西（默认全开，可用环境变量按需关）：
#   1. redis + postgres        （docker compose 起依赖，代码连 127.0.0.1）
#   2. alembic upgrade head    （建表/迁移）
#   3. FastAPI (uvicorn)       → http://127.0.0.1:54351   --reload 热更新
#   4. Recorder Agent          → http://127.0.0.1:54352   （持有可见 Playwright 浏览器）
#   5. Celery worker           （跑用例 / AI 任务 / 设备探活）
#   6. Celery beat             （定时任务调度，一个集群只能一个）
#   7. 前端 Vite dev           → http://localhost:5173    （API 代理到 54351）
#
# 用法：
#   ./start-dev.sh                      # 全开
#   START_INFRA=0 ./start-dev.sh        # 已自己起了 redis/postgres，跳过 docker
#   START_WEB=0 ./start-dev.sh          # 只起后端
#   START_WORKER=0 START_BEAT=0 ./start-dev.sh   # 只起 API + 前端（不跑异步任务）
#   API_RELOAD=0 ./start-dev.sh         # 关掉 uvicorn 热重载
#   CLEAN_STALE=0 ./start-dev.sh        # 不清理上一轮遗留进程（默认会清，防孤儿 worker）
#
# 启动前会自动杀掉上一轮遗留的本项目 celery/uvicorn 孤儿进程（防止跑旧代码）。
# Ctrl+C 一次，干净停掉所有起的进程。日志在 data/logs/dev/ 下。
# =============================================================================
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

# ---- 读取 .env（若存在）----
# DB_PASSWORD / JWT_SECRET_KEY 等敏感量都从这里来，导出后本地 app / alembic /
# celery 与 docker compose 起的 postgres 共用同一套口令。缺 .env 时给出明确提示，
# 而不是让 docker compose 抛一句难懂的 interpolation 报错。
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
else
  warn_env() { printf "\033[1;33m! %s\033[0m\n" "$*"; }
  warn_env "未找到 .env —— 现在 DB_PASSWORD / JWT_SECRET_KEY 为必填（fail-closed）。"
  echo "    在项目根建 .env，至少包含："
  echo "        DB_HOST=127.0.0.1"
  echo "        DB_USER=tap"
  echo "        DB_PASSWORD=<随机口令>"
  echo "        DB_NAME=tap"
  echo "        JWT_SECRET_KEY=<≥32位随机串>"
  echo "    生成随机值： python -c 'import secrets;print(secrets.token_urlsafe(48))'"
  exit 1
fi

# ---- 可调参数（环境变量覆盖）----
API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-54351}"
WEB_PORT="${WEB_PORT:-5173}"
RECORDER_AGENT_PORT="${RECORDER_AGENT_PORT:-54352}"
START_INFRA="${START_INFRA:-1}"        # 用 docker compose 起 redis + postgres
RUN_MIGRATIONS="${RUN_MIGRATIONS:-1}"  # 启动前跑 alembic upgrade head
START_API="${START_API:-1}"
START_RECORDER_AGENT="${START_RECORDER_AGENT:-1}"
START_WORKER="${START_WORKER:-1}"
START_BEAT="${START_BEAT:-1}"
START_WEB="${START_WEB:-1}"
API_RELOAD="${API_RELOAD:-1}"          # uvicorn --reload
CLEAN_STALE="${CLEAN_STALE:-1}"        # 启动前清理上一轮遗留的本项目进程（孤儿 worker 等）

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

# ---- 0. 清理上一轮遗留的本项目进程 ----
# make dev 重启时,上一轮若非正常退出(关终端 / 被 SIGKILL),celery worker/beat、
# uvicorn 会变成孤儿继续存活:worker 不热重载 → 消费任务时跑的是【旧代码】,造成
# "改了代码却跑旧逻辑" 的鬼打墙;beat 多实例会重复调度;uvicorn 占端口导致启动失败。
# 这里只按【本项目特有】的命令特征杀(celery_app / server.main / recorder_agent.main),
# 不会误伤别的项目。关掉:CLEAN_STALE=0 ./start-dev.sh
kill_stale() {
  local patterns=(
    "celery -A celery_app"          # celery worker + beat(核心:防跑旧代码)
    "uvicorn server.main"           # 后端 API
    "uvicorn recorder_agent.main"   # 录制 agent
  )
  local hit=0 pids
  for pat in "${patterns[@]}"; do
    pids="$(pgrep -f "$pat" 2>/dev/null | grep -vw "$$" || true)"
    if [[ -n "$pids" ]]; then
      echo "$pids" | xargs -r kill -9 2>/dev/null || true
      hit=1
    fi
  done
  # 遗留的 vite(占着 WEB_PORT,node 进程不好按名字匹配,按端口清)
  if command -v lsof >/dev/null 2>&1; then
    lsof -ti "tcp:$WEB_PORT" 2>/dev/null | grep -vw "$$" | xargs -r kill -9 2>/dev/null || true
  fi
  # 遗留的日志 tail
  pkill -9 -f "tail -n +1 -F $LOG_DIR" 2>/dev/null || true
  if [[ "$hit" == "1" ]]; then
    log "已清理上一轮遗留的本项目进程(celery / uvicorn 孤儿),避免跑旧代码"
    sleep 1   # 给端口 / broker 连接一点释放时间
  fi
}
[[ "$CLEAN_STALE" == "1" ]] && kill_stale

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
if ! "$PYBIN" -c "import alembic, uvicorn, celery, fastapi, httpx, playwright" >/dev/null 2>&1; then
  warn "当前 python 环境缺少后端或录制依赖（alembic / uvicorn / playwright 等）。"
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

export UI_RECORDER_AGENT_URL="${UI_RECORDER_AGENT_URL:-http://127.0.0.1:$RECORDER_AGENT_PORT}"

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

# ---- 2.5 起服务前自动备份（安全网，best-effort，不阻塞启动）----
# 每次起服务前把当前库 dump 到 data/backups/，只保留最近 10 份。
# 关掉：AUTO_BACKUP=0 ./start-dev.sh
AUTO_BACKUP="${AUTO_BACKUP:-1}"
if [[ "$AUTO_BACKUP" == "1" ]] && command -v docker >/dev/null 2>&1; then
  if docker ps --format '{{.Names}}' | grep -q '^tap_postgres$'; then
    mkdir -p data/backups
    _bk="data/backups/tap_autostart_$(date +%Y%m%d_%H%M%S).sql"
    if docker exec -t tap_postgres pg_dump -U "${DB_USER:-tap}" -d "${DB_NAME:-tap}" > "$_bk" 2>/dev/null && [[ -s "$_bk" ]]; then
      log "已自动备份数据库 → $_bk"
      ls -1t data/backups/tap_autostart_*.sql 2>/dev/null | tail -n +11 | xargs -r rm -f
    else
      rm -f "$_bk"   # 空库/失败不留空文件
    fi
  fi
fi

# ---- 3. 数据库初始化 / 迁移 ----
# 注意：本项目的核心表（projects 等）由 ORM create_all 建，alembic 只管增量。
# 所以空库不能裸跑 `alembic upgrade head`（第一条迁移就会因 projects 不存在而挂）。
# init_fresh_db.py 幂等处理两种情况：空库→建表+seed角色+stamp head；已有库→增量 upgrade。
# 与 docker-entrypoint.sh 的行为保持一致。
if [[ "$RUN_MIGRATIONS" == "1" ]]; then
  log "初始化/迁移数据库（scripts/init_fresh_db.py）"
  "$PYBIN" scripts/init_fresh_db.py
fi

# ---- 4. 宿主机 Recorder Agent ----
if [[ "$START_RECORDER_AGENT" == "1" ]]; then
  log "Recorder Agent → http://127.0.0.1:$RECORDER_AGENT_PORT   日志 $LOG_DIR/recorder-agent.log"
  "$PYBIN" -m uvicorn recorder_agent.main:app --host 127.0.0.1 --port "$RECORDER_AGENT_PORT" \
    > "$LOG_DIR/recorder-agent.log" 2>&1 &
  PIDS+=($!)
fi

# ---- 5. 后端 API ----
if [[ "$START_API" == "1" ]]; then
  reload_args=()
  if [[ "$API_RELOAD" == "1" ]]; then
    reload_args=(
      --reload
      --reload-dir server
      --reload-dir database
      --reload-dir runners
      --reload-dir tasks
      --reload-dir ai_gateway
      --reload-dir coding_agent
      --reload-dir utils
      --reload-dir config
      --reload-exclude "data/*"
      --reload-exclude "frontend/node_modules/*"
      --reload-exclude "frontend/dist/*"
      --reload-exclude "__pycache__/*"
      --reload-exclude "*.pyc"
    )
  fi
  log "API (uvicorn) → http://$API_HOST:$API_PORT   日志 $LOG_DIR/api.log"
  "$PYBIN" -m uvicorn server.main:app --host "$API_HOST" --port "$API_PORT" "${reload_args[@]}" \
    > "$LOG_DIR/api.log" 2>&1 &
  PIDS+=($!)
fi

# ---- 6. Celery worker ----
if [[ "$START_WORKER" == "1" ]]; then
  log "Celery worker   日志 $LOG_DIR/worker.log"
  "$PYBIN" -m celery -A celery_app worker --loglevel=INFO \
    > "$LOG_DIR/worker.log" 2>&1 &
  PIDS+=($!)
fi

# ---- 7. Celery beat ----
if [[ "$START_BEAT" == "1" ]]; then
  log "Celery beat     日志 $LOG_DIR/beat.log"
  "$PYBIN" -m celery -A celery_app beat --loglevel=INFO \
    --schedule "$PROJECT_ROOT/data/celerybeat-schedule.db" \
    > "$LOG_DIR/beat.log" 2>&1 &
  PIDS+=($!)
fi

# ---- 8. 前端 dev ----
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
echo "    Agent : http://127.0.0.1:$RECORDER_AGENT_PORT"
echo "    前端  : http://localhost:$WEB_PORT  （/api 代理到后端）"
echo "    日志  : $LOG_DIR/"
printf "\n"
log "实时日志（tail -F，Ctrl+C 退出并停服）："

# 跟随所有日志，直到 Ctrl+C
tail -n +1 -F "$LOG_DIR"/*.log &
PIDS+=($!)
wait
