# CONTRIBUTING.md

## 分支与提交

- `main/master` 只接受通过 CI 的 Pull Request；A 使用 `feature/a-*`，B 使用 `feature/b-*`，紧急修复 `fix/*`。
- 功能分支保持 1-3 天，禁止长期"大包"分支。
- contracts 变更使用单独分支，必须同时关联 A、B 消费方测试。

## PR 必填（模板见主基线 §26.2）

目的 / 范围 / 契约 / 测试 / 迁移 / 兼容 / 截图（脱敏）/ 风险与回滚。

## 本地启动

```powershell
uv sync --all-packages --group dev
pnpm install
docker compose -f docker-compose.dev.yml up -d postgres
uv run --package control-plane uvicorn app.main:app --reload --port 8000
pnpm --filter web dev
uv run --package workbench-desktop python -m app
```

## 提交前检查

```powershell
uv run pytest
uv run ruff check packages scripts tests services local-data apps
uv run mypy packages/warehouse-engine/src packages/contracts-python/src
pnpm lint
pnpm typecheck
```

## 硬性规则

- 不提交生成物、真实数据库、日志、密钥、虚拟环境和客户文件。
- 任何跨模块数据必须经过 contracts 模型与版本检查。
- 不修改他人负责模块的内部实现，通过 Issue/契约评审协调。
- 禁止 `UI -> ORM`、`UI -> HTTP`、`Engine -> SQLite/FastAPI` 等反向依赖（§41.2）。
