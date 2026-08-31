# Tasks：开发者 A M2 控制平面认证授权与商户端状态

依据：`开发需求-A平台工作台.md` §4 M2（第 4-7 周）、主基线 `开发规划与协作需求文档.md` §35.6/§35.7（云端表与迁移顺序）、§32.3（设备状态机）、§10.3（离线宽限期）、§21.4（统一错误码）、`SECURITY.md`（Token 与密码基线）、`DECISIONS.md` D-006（SSE + 30 秒轮询降级）。

- [ ] Task 0: 分支与 spec 归档
  - [ ] 0.1 从 `master`（d7e1468）创建 `feature/a-m2-control-plane-auth`
  - [ ] 0.2 写 `.trae/specs/deliver-m2-control-plane-auth/{spec,tasks,checklist}.md`

- [ ] Task 1: 云端迁移与数据字典（依赖 Task 0）
  - [ ] 1.1 `control_0002`：tenant / account / session / device（含唯一约束与状态 CHECK）
  - [ ] 1.2 `control_0003`：product_profile / license / feature_grant（同一商户只有一个 ACTIVE 许可证）
  - [ ] 1.3 `control_0004`：audit_log（提前自 §35.7 的 0006，理由见 checklist）
  - [ ] 1.4 `docs/data-dictionary.md` §2 与 `docs/er-diagram.md` §2 只加不改扩展；`control_meta.db_schema_version` 升 `control-0004`

- [ ] Task 2: 领域层（与 Task 1 并行）
  - [ ] 2.1 `account.py`：AccountRole（MERCHANT_OWNER / DEVELOPER）、密码哈希字段、失败锁定语义
  - [ ] 2.2 `session.py`：会话与刷新令牌族、轮换与重放检测语义
  - [ ] 2.3 `device.py`：device_type / fingerprint / app_version 字段与吊销终态
  - [ ] 2.4 `license.py`：状态机 + 离线宽限期评估（ACTIVE / GRACE / EXPIRED / REVOKED，默认 7 天可配置）
  - [ ] 2.5 `tenant.py`：product_profile_id 与挂起语义

- [ ] Task 3: 认证基础设施（依赖 Task 2）
  - [ ] 3.1 `infrastructure/auth/passwords.py`：Argon2id 哈希与校验
  - [ ] 3.2 `infrastructure/auth/tokens.py`：JWT 签发/校验、Refresh Token 指纹
  - [ ] 3.3 `infrastructure/auth/scopes.py`：JWT Principal 解析，dev token 下线
  - [ ] 3.4 `api/v1/deps.py`：`require_tenant_access` / `require_developer_scope` 挂真实 JWT

- [ ] Task 4: 仓储协议与双实现（依赖 Task 2）
  - [ ] 4.1 `application/ports.py`：Identity / Device / Entitlement / Audit 四个仓储协议
  - [ ] 4.2 `infrastructure/memory/repositories.py`：内存实现（测试与本地无库演示）
  - [ ] 4.3 `infrastructure/db/models.py` + `repositories.py`：PostgreSQL 实现（生产路径）
  - [ ] 4.4 组合根：按配置选择实现，默认 postgres

- [ ] Task 5: 应用层用例（依赖 Task 3、4）
  - [ ] 5.1 `auth_usecase.py`：登录 / 刷新轮换 / 注销 / me
  - [ ] 5.2 `device_usecase.py`：设备注册（幂等、吊销拒绝、max_devices 上限）
  - [ ] 5.3 `license_usecase.py`：许可证评估与宽限期
  - [ ] 5.4 `audit.py`：审计记录写入（不含敏感原文）

- [ ] Task 6: API 路由与 OpenAPI（依赖 Task 5）
  - [ ] 6.1 auth / account / devices 路由实装，成功响应统一 `{"data": ...}`
  - [ ] 6.2 心跳 / 任务 / 同步 / 配置 / telemetry / merchants 维持 stub 501
  - [ ] 6.3 重导出 `packages/contracts-schema/openapi.json`

- [ ] Task 7: SSE 状态流（依赖 Task 5）
  - [ ] 7.1 `infrastructure/realtime/hub.py`：单调事件 ID、环形缓冲、订阅广播、Last-Event-ID 续传
  - [ ] 7.2 `GET /events/stream`（15 秒 keepalive）与 `GET /events/snapshot?since=`（轮询降级）
  - [ ] 7.3 写操作（设备注册、许可证变更）向事件中心发布

- [ ] Task 8: Web 商户端（依赖 Task 6、7）
  - [ ] 8.1 登录页接真实 API，令牌持久化与登出
  - [ ] 8.2 商户工作台：许可证与授权、设备列表、版本状态；任务与同步积压标注 M3 待开放
  - [ ] 8.3 状态流 hook：SSE 主通道，失败降级 30 秒轮询，界面明示通道状态

- [ ] Task 9: 测试与质量门禁（依赖全部）
  - [ ] 9.1 认证正反例、刷新轮换与重放、租户隔离、Scope 越权、宽限期、设备上限、审计留痕
  - [ ] 9.2 仓储双实现一致性测试；SSE 续传与轮询降级测试
  - [ ] 9.3 pytest / ruff / mypy / pnpm lint / typecheck / OpenAPI 漂移门禁全绿

- [ ] Task 10: 交付文档（依赖 Task 9）
  - [ ] 10.1 `docs/m2-handover-a.md`：启动命令、验证记录、已知限制、回滚方式
  - [ ] 10.2 `DECISIONS.md` 补 M2 决策；`CHANGELOG.md` 补 A 侧 0.2.0 条目
  - [ ] 10.3 按 §26.2 模板准备 PR 描述

# Task Dependencies

- Task 0 → 全部
- Task 1、Task 2 无相互依赖，可并行
- Task 2 → Task 3、Task 4 → Task 5 → Task 6、Task 7 → Task 8 → Task 9 → Task 10
