#!/usr/bin/env bash
# =============================================================================
# 容器入口脚本
#
# 职责：
#   1. 等关键依赖（redis / postgres）健康
#   2. 跑 alembic 迁移（仅 FastAPI 角色，避免 worker / beat 重复跑）
#   3. exec 到真正的 CMD（uvicorn / celery worker / celery beat）
#
# 用法：默认由 Dockerfile 的 ENTRYPOINT 调用，不需要手动跑。
# =============================================================================
set -euo pipefail

# ---------- 工具函数 ----------
log() { echo "[entrypoint] $*"; }

wait_for() {
    # wait_for HOST PORT NAME
    local host="$1" port="$2" name="$3" max=60 i=0
    log "等待 ${name} (${host}:${port}) ..."
    until nc -z "${host}" "${port}" >/dev/null 2>&1; do
        i=$((i+1))
        if [ $i -ge $max ]; then
            log "ERROR: 等待 ${name} 超时（${max}s），放弃"
            exit 1
        fi
        sleep 1
    done
    log "✅ ${name} 已就绪"
}

# ---------- 1. 探活依赖 ----------
# 通过环境变量声明依赖；不设就跳过
[ -n "${REDIS_HOST:-}" ] && wait_for "${REDIS_HOST}" "${REDIS_PORT:-6379}" "Redis"
[ -n "${POSTGRES_HOST:-}" ] && wait_for "${POSTGRES_HOST}" "${POSTGRES_PORT:-5432}" "Postgres"
[ -n "${MYSQL_HOST:-}" ] && wait_for "${MYSQL_HOST}" "${MYSQL_PORT:-3306}" "MySQL"

# ---------- 2. 迁移（仅 FastAPI 角色跑）----------
# 判断标准：CMD 第一段是 "uvicorn"
# 想强制让某个角色跑迁移：docker run -e RUN_MIGRATIONS=1 ...
if [ "${RUN_MIGRATIONS:-auto}" = "1" ] || \
   { [ "${RUN_MIGRATIONS:-auto}" = "auto" ] && [ "${1:-}" = "uvicorn" ]; }; then
    if [ -f "alembic.ini" ]; then
        log "运行 alembic upgrade head ..."
        alembic upgrade head || {
            log "ERROR: alembic 迁移失败"
            exit 1
        }
        log "✅ 迁移完成"
    else
        log "未找到 alembic.ini，跳过迁移"
    fi
fi

# ---------- 3. exec 到真正的 CMD ----------
log "启动: $*"
exec "$@"
