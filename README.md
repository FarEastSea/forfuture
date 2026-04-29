# AI Records & Reminders

AI Records & Reminders 是一个围绕“个人动态聚合 + AI 分析提醒”构建的全栈应用。它负责抓取和整理 QQ 空间、小红书等来源的内容，沉淀到 PostgreSQL，再通过 AI 对话、动态总结和定时任务把“信息”转成“提醒”。

## 项目定位

- 统一采集 QQ 空间与小红书动态，保存文本、图片、视频和账号资料。
- 提供 Web 管理界面，集中管理 AI 配置、监控账号、NapCat 接入和系统参数。
- 支持 AI 问答、知识检索、动态总结、自然语言定时任务。
- 通过 NapCat 插件和后端 WebSocket 做消息互通，方便把提醒推送回 QQ 侧。

## AI 快速理解

如果你是第一次接手这个仓库，先记住下面几件事：

- 后端入口是 [backend/app/main.py](backend/app/main.py)，它注册所有 API、静态资源和 SPA fallback。
- 前端入口是 [frontend/src/App.vue](frontend/src/App.vue)，主导航固定为 QQ 空间、小红书、AI 对话、定时任务、设置。
- 路由定义在 [frontend/src/router/index.ts](frontend/src/router/index.ts)，主要页面就是这 5 个视图。
- 系统配置和运行状态相关接口集中在 [backend/app/routers/system.py](backend/app/routers/system.py)。
- NapCat 插件位于 [napcat-plugin-airecordsandreminders/index.mjs](napcat-plugin-airecordsandreminders/index.mjs)，默认通过 WebSocket 连接后端。
- 数据库迁移由 Alembic 管理，历史迁移文件需要保留，不能因为“线上已经执行过”就删除。
- 本仓库默认采用“服务器联调”验证结果；本地生成目录、缓存、虚拟环境和构建产物都应视为可再生文件。

## 功能概览

### 1. 动态采集

- QQ 空间抓取：内容、图片、视频、头像、评论等。
- 小红书抓取：笔记内容、媒体资源、作者信息、评论等。
- 媒体文件统一落盘到 backend/static 下的分类目录，再通过 /static 暴露。

### 2. AI 能力

- AI 对话页面支持接入 OpenAI 兼容 API。
- 动态总结服务会定期或按需生成内容摘要。
- 定时任务支持自然语言触发条件，用于提醒或通知。

### 3. 系统联动

- NapCat 插件与后端通过 WebSocket 双向通信。
- 前端通过 /api 和 /ws 访问后端。
- 后端可在检测到符合条件的内容后，把结果转成提醒消息。

## 目录速览

| 目录 | 作用 |
|------|------|
| backend/app | FastAPI 应用、路由、服务、模型 |
| backend/migrations | Alembic 迁移链 |
| backend/static | 本地媒体缓存目录，仅保留目录结构 |
| frontend/src | Vue 3 前端源码 |
| napcat-plugin-airecordsandreminders | NapCat 插件源码 |
| deploy.py | 面向远端宝塔服务器的增量部署脚本 |

## 配置原则

仓库中的示例配置、默认值和文档只允许出现占位符，不允许出现真实 IP、端口口令、Cookie、私钥路径、API Key 或测试账号口令。所有敏感值只存放于本地 `.env` 或外部环境变量。

当前项目允许 `.env` 中存在两类变量：

- 应用运行变量：数据库、后端端口、NapCat 连接地址等。
- 本地运维变量：远端部署和公网验收所需的地址、账号、凭证等。

后端配置类已允许忽略额外环境变量，因此可以把部署和验收变量与业务变量放在同一个本地 `.env` 中，而不会影响应用启动。

## 环境变量约定

### 应用运行变量

| 变量 | 说明 | 默认值/约束 |
|------|------|-------------|
| DATABASE_HOST | 数据库地址 | 127.0.0.1 |
| DATABASE_PORT | 数据库端口 | 5432 |
| DATABASE_NAME | 数据库名 | AI_records_and_reminders |
| DATABASE_USER | 数据库用户 | AI_records_and_reminders |
| DATABASE_PASSWORD | 数据库密码 | 仅写入本地 `.env` |
| BACKEND_HOST | 后端监听地址 | 0.0.0.0 |
| BACKEND_PORT | 后端监听端口 | 18100 |
| SECRET_KEY | 会话/签名密钥 | 仅写入本地 `.env` |
| NAPCAT_WS_URL | NapCat WebSocket 地址 | ws://127.0.0.1:3001 |
| NAPCAT_TOKEN | NapCat 令牌 | 仅写入本地 `.env` |
| STATIC_DIR | 静态资源目录 | ./static |

### 远端部署变量

`deploy.py` 会读取以下变量：

| 变量 | 说明 |
|------|------|
| DEPLOY_REMOTE_HOST | 远端服务器地址 |
| DEPLOY_REMOTE_PORT | SSH 端口 |
| DEPLOY_REMOTE_USER | SSH 用户 |
| DEPLOY_SSH_KEY_PATH | SSH 私钥路径 |
| DEPLOY_REMOTE_PASSWORD | 可选，只有不用私钥时才填写 |
| DEPLOY_REMOTE_BASE | 远端项目目录 |
| DEPLOY_PYTHON_BIN | 远端 Python 解释器 |
| DEPLOY_BACKEND_PORT | 远端后端服务端口 |

### 公网验收变量

建议把公网测试信息也放在本地 `.env`，避免写进文档或脚本：

| 变量 | 说明 |
|------|------|
| REMOTE_SMOKE_BASE_URL | 公网验收地址 |
| REMOTE_SMOKE_USERNAME | 测试用户名 |
| REMOTE_SMOKE_PASSWORD | 测试密码 |

## 服务器联调流程

这是当前推荐流程，不再以本地完整开发环境作为默认验证路径。

### 1. 修改代码

- 后端改动以 [backend/app](backend/app) 为主。
- 前端改动以 [frontend/src](frontend/src) 为主。
- 插件改动以 [napcat-plugin-airecordsandreminders](napcat-plugin-airecordsandreminders) 为主。

### 2. 准备部署环境变量

- 复制 [.env.example](.env.example) 到本地 `.env`。
- 只在本地 `.env` 中填写部署变量和公网验收变量。
- 不要把真实值写入 README、脚本常量、示例配置或 Git 历史。

### 3. 部署到宝塔服务器

执行 `deploy.py` 前，本机需要具备以下前置条件：

- 系统 Python（用于运行脚本本身，不要求使用 `.venv`）
- OpenSSH Client，提供 `ssh` / `scp`
- Node.js 与 npm，用于自动安装前端依赖并执行构建

在项目根目录执行：

```bash
python deploy.py
```

部署脚本会执行这些步骤：

- 使用系统自带的 `ssh` / `scp`，不依赖本地 `.venv`。
- 如果 `frontend/node_modules/` 不存在，先自动执行 `npm install`。
- 自动执行 `npm run build` 生成最新的 `frontend/dist/`。
- 上传后端指定文件。
- 上传前端构建产物 `frontend/dist/`。
- 在远端执行 `alembic upgrade head`。
- 重载或重启 Gunicorn。
- 调用远端 `/api/health` 做健康检查。

推荐使用 SSH 私钥认证。为了避免在脚本中处理交互式密码输入，不建议把 SSH 密码写入 `.env`。

### 4. 公网验收

- 在浏览器访问 `REMOTE_SMOKE_BASE_URL` 指向的公网地址。
- 使用本地 `.env` 中的测试账号完成登录。
- 至少检查首页可访问、登录可用、设置页可打开、QQ/小红书页能正常加载。
- 如果涉及 NapCat 联动，再检查前端右下角状态和系统状态接口。

## 宝塔部署要点

### 后端

```bash
cd /www/wwwroot/AI_records_and_reminders/backend

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
playwright install-deps
alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 18100
```

建议以 Supervisor 或宝塔 Python 项目守护方式运行，工作目录指向 `/www/wwwroot/AI_records_and_reminders/backend`。

### 前端

```bash
cd /www/wwwroot/AI_records_and_reminders/frontend
npm install
npm run build
```

Nginx 至少需要把 `/api/`、`/ws/` 和 `/static/` 代理到后端，把 `/` 交给前端 `index.html`。

### NapCat 插件

```bash
cd /www/wwwroot/AI_records_and_reminders/napcat-plugin-airecordsandreminders
npm install
```

之后把整个插件目录复制到 NapCat 插件目录并重启 NapCat。

## 清理策略

下面这些内容属于可再生或本地专用文件，应清理或保持忽略：

- `.venv/`
- `frontend/node_modules/`
- `frontend/dist/`
- `.omc/`
- `.vscode/`
- `__pycache__/`、测试缓存、构建缓存、日志文件、PID 文件
- `backend/static` 下抓取得到的媒体文件

下面这些内容不应删除：

- `.env`：本地敏感配置承载文件
- `.env.example`：示例模板
- `deploy.py`：远端部署脚本
- `backend/migrations/versions/*.py`：数据库迁移链
