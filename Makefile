# XianyuAutoAgent · 一键打包/启动
# 常用：make up（打包+启动）· make logs（看日志）· make restart（换cookie后）

# 兼容 docker compose v2 与旧版 docker-compose
DOCKER_COMPOSE := $(shell docker compose version >/dev/null 2>&1 && echo "docker compose" || echo "docker-compose")
# 优先用项目 venv（见 README/环境准备）；没有则回退系统 python3
PYTHON ?= $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)

.DEFAULT_GOAL := help
.PHONY: help env build up down restart logs ps test smoke clean token

help: ## 显示所有命令
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

env: ## 首次配置：从 .env.example 生成 .env（已存在则跳过）
	@if [ -f .env ]; then \
		echo ".env 已存在，跳过（如需重置请手动删除）"; \
	else \
		cp .env.example .env && echo "已生成 .env，请编辑填入 COOKIES_STR 及后端配置"; \
	fi

build: ## 构建镜像（本地代码打包，不用上游镜像）
	$(DOCKER_COMPOSE) build

up: ## 一键打包并后台启动（缺 .env 会先报错提示）
	@if [ ! -f .env ]; then \
		echo "❌ 缺少 .env（否则 Docker 会把它挂载成目录导致启动失败）"; \
		echo "   先执行: make env && vim .env"; \
		exit 1; \
	fi
	$(DOCKER_COMPOSE) up -d --build
	@echo "✅ 已启动，查看日志: make logs"

down: ## 停止并移除容器
	$(DOCKER_COMPOSE) down

restart: ## 重启容器（改完 .env 后用，不重新构建）
	$(DOCKER_COMPOSE) restart
	@echo "✅ 已重启，查看日志: make logs"

logs: ## 跟踪日志
	$(DOCKER_COMPOSE) logs -f --tail=100

ps: ## 查看容器状态
	$(DOCKER_COMPOSE) ps

test: ## 跑测试套件（需本地 python 环境有依赖）
	$(PYTHON) -m unittest discover -s tests -v

smoke: ## Coze 连通性冒烟（需 .env 已配 COZE_API_TOKEN/COZE_BOT_ID）
	$(PYTHON) scripts/coze_smoke_test.py "你好，这个还在吗？"

token: ## 宿主机开真Chrome手动过滑块+重铸Cookie写回.env（撞RGV587时用）。需先 pip install playwright && playwright install chromium
	$(PYTHON) scripts/fetch_token_browser.py

clean: ## 停容器并删除本地镜像
	$(DOCKER_COMPOSE) down --rmi local
