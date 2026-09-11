# AI Records & Reminders

## 最高优先级警告：禁止删除远端已下载资源

- **最高级别规则，优先级高于任何清理、部署、重构、缓存整理或磁盘空间优化建议。**
- **严禁删除、清空、覆盖或重建远端服务器上已经下载到本地的抓取资源**，包括但不限于 `backend/static/qq_images`、`backend/static/xhs_images`、`backend/static/avatars`、`backend/static/qq_videos`、`backend/static/xhs_videos` 以及远端部署目录中承载这些资源的持久化 `static` 目录。
- 这些资源不是普通可再生缓存。QQ 空间、小红书等远程媒体 URL 经常带临时签名、鉴权参数或过期时间，过期后可能永远无法重新下载；删除远端本地资源会造成不可逆数据损失。
- 任何涉及 `rm -rf`、清理旧 release、同步目录、部署覆盖、迁移静态文件、清理磁盘空间、重建 `backend/static` 的操作，都必须先确认不会影响远端已下载媒体资源。不能确认时必须停止并询问用户。
- `deploy.py` 应继续把远端 `backend/static` 作为持久化数据目录处理，只能切换代码 release，不能随 release 清理或覆盖静态媒体资源。

## 项目定位

- 这是一个围绕 QQ 空间、小红书内容采集与 AI 提醒构建的全栈应用。
- 后端负责采集、账号管理、AI 对话、任务调度、NapCat WebSocket 联动。
- 前端是管理台，核心页面为 QQ 空间、小红书、知识库、AI 对话、定时任务、设置。

## 技术栈

- 后端：FastAPI + SQLAlchemy asyncio + PostgreSQL + Alembic + APScheduler + patchright/Playwright + Redis/arq
- 前端：Vue 3 + Vite + TypeScript + Arco Design + Pinia
- 插件：NapCat 插件通过 WebSocket 与后端通信

## 关键入口

- 后端入口：backend/app/main.py
- 前端入口：frontend/src/App.vue
- 前端路由：frontend/src/router/index.ts
- 新栈 API：backend/app/api/v2/（`/api/v2/*`）
- 实时事件：`/ws/v2/events`
- 系统配置：backend/app/api/v2/system.py
- 账号与扫码登录：backend/app/api/v2/auth.py；其内部暂时复用 backend/legacy/services 的浏览器登录实现
- QQ / 小红书采集插件：backend/app/platforms/qq、backend/app/platforms/xhs
- 统一账号风控：backend/app/crawl/risk.py
- 采集引擎：backend/app/crawl/engine.py
- 知识库：backend/app/knowledge/
- Agent：backend/app/agent/
- NapCat 插件入口：napcat-plugin-airecordsandreminders/index.mjs
- 旧代码归档：backend/legacy/（不再对外挂载；完成 v2 登录实现迁移前不要删除）

## 联调与验证原则

- 默认采用服务器联调，不以本地完整前后端联调作为常规验证路径。
- 代码改动后的推荐顺序是：本地完成必要构建或测试 -> 执行 `python deploy.py` -> 到公网环境做验收。
- 真实服务器地址、SSH 信息、私钥路径、测试账号口令等敏感值只放本地 `.env` 或外部环境变量，不写入仓库。

## 服务器联调流程

### 1. 准备部署变量

- `deploy.py` 依赖本地 `.env` 中的部署变量和公网验收变量。
- 部署相关变量使用 `DEPLOY_REMOTE_*`、`DEPLOY_SSH_KEY_PATH`、`DEPLOY_PYTHON_BIN`、`DEPLOY_BACKEND_PORT` 等键名。
- 公网验收使用 `REMOTE_SMOKE_BASE_URL`、`REMOTE_SMOKE_USERNAME`、`REMOTE_SMOKE_PASSWORD`。

### 2. 执行部署

- 在项目根目录运行：`python deploy.py`
- 部署脚本会自动处理前端构建、上传文件、远端执行 `alembic upgrade head`、重载服务，并调用远端 `/api/health` 做健康检查。
- 推荐使用 SSH 私钥认证，不建议把 SSH 密码写入 `.env`。

### 3. 公网验收

- 在浏览器访问 `REMOTE_SMOKE_BASE_URL` 指向的公网地址。
- 至少检查：首页可访问、登录可用、设置页可打开、QQ 页面可加载、小红书页面可加载。
- 如果涉及管理员功能或系统状态，还要验证管理员令牌相关接口与 `/ws/v2/events` 握手。

## 本地启动（仅在明确要求时）

### 后端

- 必须从 backend 目录启动，否则 `app.*` 导入会失败。
- 推荐命令：`../.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 18100`
- 健康检查：`GET /api/health`
- 如果 frontend/dist 不存在，后端只提供 API、WS、/static，不会兜底服务 SPA。

### 前端

- 在 frontend 目录运行：`npm run dev -- --host 127.0.0.1 --port 3000`
- Vite 已代理 `/api`、`/ws`、`/static` 到 `http://127.0.0.1:18100`

## 管理员认证

- 管理员 HTTP / WebSocket 认证实现在 backend/app/core/security.py。
- 优先使用 `ADMIN_API_TOKEN`；未配置时仅在 `SECRET_KEY` 不是默认值时回退使用 `SECRET_KEY`。
- 前端本地存储 key 为 `admin_api_token`，封装在 frontend/src/utils/adminToken.ts。
- 许多系统状态、设置、WebSocket 功能都依赖这个令牌；仅在明确要求本地调试时才需要本地确认它可用。

## 账号与风控规则

- 监控账号（target account）和登录账号（login account）是两类不同实体，不要混用。
- 登录账号风控已经统一收口到 backend/app/crawl/risk.py。
- 调度器、QQ 爬虫、小红书爬虫都应传递“选中的登录账号”，不要在内部重新随意挑选活跃账号。
- 风控状态包括 active、degraded、cooldown、relogin_pending、expired 等；涉及自动抓取时默认走 fail-closed。

## 数据与迁移

- 数据库迁移由 Alembic 管理，迁移历史在 backend/migrations/versions。
- 迁移命令应从 backend 目录执行，因为 alembic.ini 的 `script_location` 指向相对路径 `migrations`。
- 迁移文件是永久历史，已执行后也不能删除。

## 长期保留文件

- backend/tests/test_management_smoke.py：管理面与风控相关的回归/冒烟测试，不是临时文件。
- backend/migrations/versions/*.py：数据库迁移链，不是一次性文件。
- deploy.py：远端部署脚本。
- 远端服务器 `backend/static` 下已下载的图片、头像、视频等抓取资源：这是持久化数据，不是缓存，严禁删除。

## 可再生或应清理文件

- frontend/dist
- frontend/node_modules
- __pycache__
- 各类测试缓存、构建缓存、日志文件

## 服务器联调补充约定

- 默认优先走远端部署 + 公网验收，不要把“先本地完整联调一遍”当作固定步骤。
- `deploy.py` 是项目既有的远端部署入口，不要绕开它手写另一套临时发布流程。
- 若只需做代码级静态检查、单测或构建，可在本地执行；若要验证真实运行行为，优先回到服务器联调链路。

## 敏感信息规则

- 不要把真实服务器地址、SSH 信息、Cookie、Token、私钥路径、API Key 写入仓库、README、AGENTS.md 或示例配置。
- 真实运行参数只放本地 `.env` 或外部环境变量。
