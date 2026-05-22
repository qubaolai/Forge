.PHONY: help install install-rag install-all install-dev shell update lock \
        run dev test test-unit test-integration test-cov \
        lint fmt type-check check \
        db-init clean build env-setup

POETRY ?= poetry
APP_ENV ?= dev

help:
	@echo "make install         — 安装核心依赖"
	@echo "make install-rag     — 安装核心 + RAG extras"
	@echo "make install-all     — 安装核心 + 所有 extras + dev 组"
	@echo "make install-dev     — 安装核心 + dev 组"
	@echo "make env-setup       — 从 .example 文件初始化本地 .env"
	@echo "make dev             — 开发模式启动 (APP_ENV=dev, uvicorn --reload)"
	@echo "make run             — 生产模式启动"
	@echo "make test            — 跑全部测试 (APP_ENV=test)"
	@echo "make test-unit       — 跑单元测试"
	@echo "make check           — lint + type-check + test (CI 等价)"

install:
	$(POETRY) install --no-root

install-rag:
	$(POETRY) install --extras rag

install-all:
	$(POETRY) install --extras all --with dev

install-dev:
	$(POETRY) install --with dev

shell:
	$(POETRY) shell

update:
	$(POETRY) update

lock:
	$(POETRY) lock --no-update

env-setup:
	@test -f .env || cp .env.example .env && echo ".env created"
	@test -f .env.dev || cp .env.dev.example .env.dev 2>/dev/null || true

# ── 启动 ───────────────────────────────────────────────────────────────────────
run:
	APP_ENV=$(APP_ENV) $(POETRY) run uvicorn forge.api.server:app --host 0.0.0.0 --port 8000

dev:
	APP_ENV=$(APP_ENV) $(POETRY) run uvicorn forge.api.server:app \
		--host 0.0.0.0 --port 8000 --reload

# ── 测试 ───────────────────────────────────────────────────────────────────────
test:
	APP_ENV=test $(POETRY) run pytest tests/ -v

test-unit:
	APP_ENV=test $(POETRY) run pytest tests/unit/ -v

test-integration:
	APP_ENV=test $(POETRY) run pytest tests/integration/ -v

test-cov:
	APP_ENV=test $(POETRY) run pytest tests/ --cov=src/forge --cov-report=html --cov-report=term

# ── 代码质量 ──────────────────────────────────────────────────────────────────
lint:
	$(POETRY) run ruff check src/ tests/

fmt:
	$(POETRY) run ruff check --fix src/ tests/
	$(POETRY) run ruff format src/ tests/

type-check:
	$(POETRY) run mypy src/

check: lint type-check test-unit

# ── DB ────────────────────────────────────────────────────────────────────────
db-init:
	APP_ENV=$(APP_ENV) $(POETRY) run python -m forge.infrastructure.db.init

# ── 清理 ──────────────────────────────────────────────────────────────────────
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov dist

build:
	$(POETRY) build
