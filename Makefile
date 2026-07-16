# 测试自动化平台 —— 常用本地命令入口
# 用法：make dev / make stop / make migrate / make lint / make build

.PHONY: help venv setup dev stop migrate db-backup db-restore lint build backfill-flags check-flags

# PIP_TRUSTED=1 时跳过 pip 的 SSL 证书校验（公司网络做 SSL 检查 / 证书装不全时用）
PIP_TRUSTED ?= 0
PIP_TRUSTED_FLAGS = $(if $(filter 1,$(PIP_TRUSTED)),--trusted-host pypi.org --trusted-host files.pythonhosted.org --trusted-host pypi.python.org,)

help:
	@echo "可用命令："
	@echo "  make venv     用最新 Python（3.13优先）重建 venv（3.9 装不了 numpy 2.2）"
	@echo "  make setup    安装后端(venv) + 前端依赖（首次必跑）"
	@echo "  make dev      启动本地开发环境（依赖 + API + worker + beat + 前端）"
	@echo "  make stop     停止本地开发环境的残留进程"
	@echo "  make migrate  初始化/迁移数据库（空库建表+stamp，已有库增量）"
	@echo "  make db-backup            pg_dump 备份到 data/backups/"
	@echo "  make db-restore FILE=xxx  从备份文件恢复"
	@echo "  make lint     前端 eslint + 后端 ruff 检查"
	@echo "  make build    前端构建（tsc -b + vite build）"
	@echo "  make backfill-flags           历史 AI 诊断回填成用例标记（幂等可重跑）"
	@echo "  make check-flags [MODULE=20]  AI 标记链路只读诊断"

# 用 3.10+（优先 3.12，对齐 Dockerfile）重建 venv
venv:
	@PY=$$(for p in python3.13 python3.12 python3.11 python3.10; do command -v $$p >/dev/null 2>&1 && echo $$p && break; done); \
	if [ -z "$$PY" ]; then \
		echo "✗ 未找到 Python 3.10+（numpy 2.2 / opencv 需要）。请先安装最新版，例如： brew install python@3.13"; \
		exit 1; \
	fi; \
	echo "▶ 用 $$PY 重建 venv"; \
	rm -rf venv && $$PY -m venv venv && venv/bin/python -m pip install --upgrade pip $(PIP_TRUSTED_FLAGS); \
	echo "✓ venv 就绪，接着跑： make setup"

setup:
	venv/bin/python -m pip install --upgrade pip $(PIP_TRUSTED_FLAGS)
	venv/bin/pip install -r requirements.txt $(PIP_TRUSTED_FLAGS)
	cd frontend && npm install

dev:
	./start-dev.sh

stop:
	./stop-dev.sh

migrate:
	@if [ -x venv/bin/python ]; then PY=venv/bin/python; else PY=$$(command -v python3 || command -v python); fi; \
	if ! $$PY -c "import alembic" >/dev/null 2>&1; then \
		echo "✗ 当前 Python 没装 alembic。先跑： make setup（或 source venv/bin/activate 后再 make migrate）"; exit 1; \
	fi; \
	echo "▶ $$PY scripts/init_fresh_db.py（空库建表+stamp / 已有库增量 upgrade）"; \
	$$PY scripts/init_fresh_db.py

# 备份数据库到 data/backups/（用 docker exec 进 postgres 容器 pg_dump，免装本机 pg 客户端）
db-backup:
	@mkdir -p data/backups
	@set -a; [ -f .env ] && . ./.env; set +a; \
	if ! docker ps --format '{{.Names}}' | grep -q '^tap_postgres$$'; then \
		echo "✗ 找不到运行中的 tap_postgres 容器，先 make dev 或 docker compose up -d postgres"; exit 1; \
	fi; \
	ts=$$(date +%Y%m%d_%H%M%S); f=data/backups/tap_$$ts.sql; \
	docker exec -t tap_postgres pg_dump -U $${DB_USER:-tap} -d $${DB_NAME:-tap} > $$f \
		&& echo "✓ 已备份到 $$f （$$(du -h $$f | cut -f1)）" \
		|| (echo "✗ 备份失败"; rm -f $$f; exit 1)

# 从备份恢复： make db-restore FILE=data/backups/tap_YYYYmmdd_HHMMSS.sql
db-restore:
	@test -n "$(FILE)" || (echo "用法： make db-restore FILE=data/backups/xxx.sql"; exit 1)
	@test -f "$(FILE)" || (echo "✗ 文件不存在： $(FILE)"; exit 1)
	@set -a; [ -f .env ] && . ./.env; set +a; \
	if ! docker ps --format '{{.Names}}' | grep -q '^tap_postgres$$'; then \
		echo "✗ 找不到运行中的 tap_postgres 容器，先 make dev 或 docker compose up -d postgres"; exit 1; \
	fi; \
	docker exec -i tap_postgres psql -U $${DB_USER:-tap} -d $${DB_NAME:-tap} < "$(FILE)" \
		&& echo "✓ 已从 $(FILE) 恢复"

# 历史 AI 诊断（ai_runs.feature=api_report_fix）→ 用例标记；幂等，可重复跑
backfill-flags:
	@if [ -x venv/bin/python ]; then PY=venv/bin/python; else PY=$$(command -v python3 || command -v python); fi; \
	echo "▶ $$PY -m database.migrations.data_migrations.backfill_ai_case_flags --commit"; \
	$$PY -m database.migrations.data_migrations.backfill_ai_case_flags --commit

# AI 标记链路只读诊断；可选 MODULE=<模块id> 检查指定模块
check-flags:
	@if [ -x venv/bin/python ]; then PY=venv/bin/python; else PY=$$(command -v python3 || command -v python); fi; \
	$$PY -m database.migrations.data_migrations.check_ai_flags $(if $(MODULE),--module-id $(MODULE),)

lint:
	cd frontend && npm run lint
	@command -v ruff >/dev/null 2>&1 && ruff check . || echo "⚠ ruff 未安装（pip install ruff），跳过后端 lint"

build:
	cd frontend && npm run build
