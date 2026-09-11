# Contributing

感谢参与 AI Records & Reminders。

## 开发流程

1. 从 `main` 创建短期功能分支。
2. 保持改动聚焦，并为行为变化补充测试。
3. 后端执行 `python -m pytest tests -q`。
4. 前端执行 `npm ci && npm run build`。
5. 提交 Pull Request，说明变更目的、验证结果和部署影响。

## 数据安全

- 禁止提交真实 `.env`、Cookie、Token、API Key、二维码、账号口令、服务器地址或私钥。
- 禁止提交抓取到的个人内容和媒体。
- 远端 `backend/static` 中的已下载媒体是持久化数据，不得在部署、清理或回滚时删除、覆盖或重建。
- `backend/legacy/` 和既有 Alembic 迁移仍承担兼容职责，未完成依赖迁移前不得删除。

## 代码约定

- 修复根因，避免用特例掩盖错误状态。
- 新业务接口使用 `/api/v2/*`，实时事件使用 `/ws/v2/events`。
- 账号和采集状态采用 fail-closed；不要把二维码 HTTP 200 当作真实登录或采集成功。
- 新增配置时同步更新 `.env.example` 和 README，示例值必须是无效占位符。
