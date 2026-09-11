# AI Records & Reminders

面向个人内容归档的全栈应用：采集经授权的 QQ 空间和小红书内容，统一保存文本与媒体，建立全文/向量知识库，并通过 AI 对话、定时任务和 NapCat 通知把历史记录转化为可检索的信息与提醒。

> 本项目仅用于管理本人或已获明确授权的数据。使用采集功能时，请遵守当地法律、平台服务条款和账号安全规则。不要用于绕过访问控制、批量滥用或未授权的数据收集。

## 功能

- QQ 空间与小红书账号、登录状态和采集任务管理
- 文本、图片、视频、评论及作者信息的统一持久化
- PostgreSQL 全文索引与向量检索，支持增量补齐缺失向量
- OpenAI 兼容的聊天与嵌入模型配置
- AI Agent 对话、可审计工具调用和自然语言定时任务
- Redis/arq 后台任务、WebSocket 实时进度和 NapCat 通知
- Vue 3 管理台：QQ、小红书、知识库、AI 对话、任务与系统设置

## 当前状态

项目采用 `/api/v2/*` 作为对外业务 API，实时事件入口为 `/ws/v2/events`。`backend/legacy/` 仅保留给 v2 扫码登录内部复用和旧数据兼容，不再直接挂载旧接口；在完成登录实现迁移前请勿删除。

QQ 采集链路已完成真实服务器验收。小红书登录和采集可能受到平台对账号、设备、浏览器或 IP 的风险控制，不能仅凭二维码接口成功判断真实采集可用。

## 技术栈

| 层级 | 技术 |
| --- | --- |
| 后端 | FastAPI、SQLAlchemy asyncio、Alembic、PostgreSQL |
| 任务 | Redis、arq、APScheduler |
| 采集 | Patchright/Playwright、curl-cffi、aiohttp |
| AI/知识库 | OpenAI-compatible API、jieba、NumPy |
| 前端 | Vue 3、TypeScript、Vite、Pinia、Arco Design |
| 集成 | NapCat WebSocket 插件 |

## 目录

```text
backend/
  app/          # v2 后端：API、领域模型、采集、知识库、Agent
  legacy/       # 登录复用与旧数据兼容层
  migrations/  # 不可删除的 Alembic 迁移历史
  scripts/      # 数据迁移、校验和知识库索引脚本
frontend/       # Vue 3 管理台
napcat-plugin-airecordsandreminders/  # NapCat 插件
scripts/        # 运维与远端核查工具
deploy.py       # 宝塔服务器部署入口
```

## 环境要求

- Python 3.12
- Node.js 20+ 与 npm
- PostgreSQL 16+
- Redis 7+（或兼容实现）
- Chromium/Chrome；涉及扫码和浏览器采集时需要

## 快速开始

### 1. 配置环境

```bash
cp .env.example .env
```

必须替换示例密码和密钥。真实数据库口令、Redis 密码、Cookie、Token、API Key、服务器地址和私钥路径只能保存在本地 `.env` 或部署环境中，不能提交到仓库。

### 2. 初始化后端

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
cd backend
alembic upgrade head
python -m uvicorn app.main:app --host 127.0.0.1 --port 18100
```

Windows PowerShell 激活命令为：

```powershell
.\.venv\Scripts\Activate.ps1
```

### 3. 启动前端

```bash
cd frontend
npm ci
npm run dev -- --host 127.0.0.1 --port 3000
```

Vite 会把 `/api`、`/ws` 和 `/static` 代理到本地后端。

### 4. 启动 arq worker

在项目根目录、使用与后端相同的环境变量执行：

```bash
cd backend
arq app.crawl.worker.WorkerSettings
```

## 关键配置

完整模板见 [.env.example](.env.example)。常用变量如下：

| 变量 | 用途 |
| --- | --- |
| `DATABASE_HOST/PORT/NAME/USER/PASSWORD` | PostgreSQL 连接 |
| `REDIS_URL` | Redis/arq、限流与进度广播 |
| `SECRET_KEY` | 会话与签名密钥 |
| `ADMIN_API_TOKEN` | 管理 API 与 WebSocket 鉴权，建议单独配置 |
| `NAPCAT_WS_URL` / `NAPCAT_TOKEN` | NapCat 接入 |
| `STATIC_DIR` | 持久化媒体目录 |
| `BROWSER_PROFILE_DIR` | 浏览器登录状态目录 |

AI 供应商、聊天模型、嵌入模型和行为参数在管理台中配置并保存到数据库。生产环境必须使用强随机密钥，并限制数据库、Redis 和管理 API 的网络暴露范围。

## 数据库与知识库

迁移必须从 `backend` 目录执行：

```bash
alembic upgrade head
```

对已有内容增量建立全文索引并只补缺失向量：

```bash
python scripts/index_knowledge.py
```

仅在更换嵌入模型并明确希望重算全部向量时使用 `--force`。该操作会把知识分块发送到所配置的外部 AI 网关，请先确认数据处理范围和授权。

## 测试与构建

```bash
cd backend
python -m pytest tests -q

cd ../frontend
npm ci
npm run build
```

GitHub Actions 会运行同样的后端测试和前端生产构建。

## 宝塔部署

生产环境的主服务和 arq worker 均由宝塔 Python 项目管理器守护。准备好本地 `.env` 中的 `DEPLOY_*` 和 `REMOTE_SMOKE_*` 变量后，在项目根目录执行：

```bash
python deploy.py
```

部署脚本会构建前端、创建不可变 release、执行 Alembic、切换 `backend/current` 软链接、重载宝塔服务并检查后端本机及公网健康状态。

远端 `backend/static`、`backend/browser_data` 和 `backend/browser_runtime` 是跨 release 的持久化目录。尤其是已经下载的媒体可能无法再次获取，任何部署、清理或回滚都不得删除、覆盖或重建这些目录。

## 安全与隐私

- 不要提交 `.env`、Cookie、二维码内容、Token、API Key、SSH 私钥或真实服务器信息。
- 不要把远端已下载媒体当作可再生缓存。
- 管理接口应配置 `ADMIN_API_TOKEN`，数据库和 Redis 不应直接暴露到公网。
- 发现安全问题请按 [SECURITY.md](SECURITY.md) 私下报告，不要在公开 Issue 中附带凭据或个人数据。

## 参与贡献

开发流程、测试要求和数据保护规则见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## License

[MIT](LICENSE) © 2026 FarEastSea
