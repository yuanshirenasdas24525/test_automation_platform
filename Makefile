# 测试自动化平台 —— 常用本地命令入口
# 用法：make dev / make stop / make migrate / make lint / make build

.PHONY: help venv setup dev stop migrate lint build

# PIP_TRUSTED=1 时跳过 pip 的 SSL 证书校验（公司网络做 SSL 检查 / 证书装不全时用）
PIP_TRUSTED ?= 0
PIP_TRUSTED_FLAGS = $(if $(filter 1,$(PIP_TRUSTED)),--trusted-host pypi.org --trusted-host files.pythonhosted.org --trusted-host pypi.python.org,)

help:
	@echo "可用命令："
	@echo "  make venv     用最新 Python（3.13优先）重建 venv（3.9 装不了 numpy 2.2）"
	@echo "  make setup    安装后端(venv) + 前端依赖（首次必跑）"
	@echo "  make dev      启动本地开发环境（依赖 + API + worker + beat + 前端）"
	@echo "  make stop     停止本地开发环境的残留进程"
	@echo "  make migrate  alembic upgrade head"
	@echo "  make lint     前端 eslint 检查"
	@echo "  make build    前端构建（tsc -b + vite build）"

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
	echo "▶ $$PY -m alembic upgrade head"; \
	$$PY -m alembic upgrade head

lint:
	cd frontend && npm run lint

build:
	cd frontend && npm run build
