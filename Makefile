.PHONY: help up down restart build logs ps \
        fe-build fe-sync fe-deploy \
        ollama-start ollama-stop ollama-status \
        db-shell redis-cli

FRONTEND_SRC := frontend-src
FRONTEND_DIST := frontend/dist

# ─────────────────────────────────────────────
# 默认目标：显示帮助
# ─────────────────────────────────────────────
help:
	@echo ""
	@echo "  QuantTrading — 常用命令"
	@echo ""
	@echo "  Docker 服务"
	@echo "    make up            启动所有服务（后台）"
	@echo "    make down          停止并移除容器"
	@echo "    make restart       重启所有服务"
	@echo "    make restart s=backend  只重启指定服务（s= 指定名称）"
	@echo "    make build         重新构建所有镜像并启动"
	@echo "    make fe-deploy     前端全链路：构建源码 → 同步 dist → 重建容器"
	@echo "    make logs          实时查看后端日志"
	@echo "    make logs s=frontend  查看指定服务日志"
	@echo "    make ps            查看各容器状态"
	@echo ""
	@echo "  Ollama（本地 LLM）"
	@echo "    make ollama-start  启动 Ollama 后台服务"
	@echo "    make ollama-stop   停止 Ollama，释放内存和电量"
	@echo "    make ollama-status 查看 Ollama 运行状态及已加载模型"
	@echo ""
	@echo "  调试工具"
	@echo "    make db-shell      进入 PostgreSQL 交互终端"
	@echo "    make redis-cli     进入 Redis 交互终端"
	@echo ""

# ─────────────────────────────────────────────
# Docker 服务管理
# ─────────────────────────────────────────────
up:
	docker-compose up -d

down:
	docker-compose down

restart:
ifdef s
	docker-compose restart $(s)
else
	docker-compose restart
endif

build:
	docker-compose up -d --build

logs:
ifdef s
	docker-compose logs -f $(s)
else
	docker-compose logs -f backend
endif

ps:
	docker-compose ps

# ─────────────────────────────────────────────
# 前端构建流水线
# ─────────────────────────────────────────────

# 1. 在 frontend-src/ 中编译 Vue 源码
fe-build:
	cd $(FRONTEND_SRC) && npm run build

# 2. 将编译产物同步到 Docker 挂载目录
fe-sync:
	cp -r $(FRONTEND_SRC)/dist/. $(FRONTEND_DIST)/

# 3. 一键全流程：编译 → 同步 → 重建容器
fe-deploy: fe-build fe-sync
	docker-compose up -d --build frontend
	@echo "前端已更新，访问 http://localhost:8888"

# ─────────────────────────────────────────────
# Ollama 管理（Ollama.app，macOS）
# ─────────────────────────────────────────────

# 启动 Ollama（如果已在运行则跳过）
ollama-start:
	@if pgrep -x "ollama" > /dev/null; then \
		echo "Ollama 已在运行"; \
	else \
		open -a Ollama && echo "Ollama 启动中，稍等几秒..."; \
	fi

# 停止 Ollama，彻底释放显存/内存
ollama-stop:
	@pkill -f "Ollama.app" 2>/dev/null || true
	@pkill -x "ollama" 2>/dev/null || true
	@echo "Ollama 已停止，内存已释放"

# 查看状态：进程 + 当前加载的模型
ollama-status:
	@echo "── 服务状态 ──"
	@pgrep -x "ollama" > /dev/null && echo "  运行中 (PID: $$(pgrep -x ollama))" || echo "  未运行"
	@echo ""
	@echo "── 已加载模型 ──"
	@curl -s http://localhost:11434/api/ps 2>/dev/null | python3 -c \
		"import sys,json; d=json.load(sys.stdin); \
		 [print('  '+m['name']+' ('+str(round(m.get('size',0)/1e9,1))+'GB)') \
		  for m in d.get('models',[])] or print('  无模型在内存中')" \
		2>/dev/null || echo "  Ollama 未运行"

# ─────────────────────────────────────────────
# 调试工具
# ─────────────────────────────────────────────
db-shell:
	docker exec -it quantdinger-db psql -U postgres -d quantdinger

redis-cli:
	docker exec -it quantdinger-redis redis-cli
