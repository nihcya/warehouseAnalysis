# DECISIONS.md：已冻结的技术决策（M0，2026-08-29）

本文件记录主基线《开发规划与协作需求文档.md》V2.0 §18 决策清单中与 A 相关的冻结结论。
变更任何决策必须新建 ADR 并经 A、B 共同评审，不得在聊天记录中口头修改。

| # | 决策 | 结论 | 日期 | 依据 | 撤销条件 |
|---|---|---|---|---|---|
| D-001 | 第一版工作台数量 | 严格限制为一个商户主工作台；UI、同步、调度通过同一本地应用服务串行写入 | 2026-08-29 | 基线 §18/§35.3 | 多工作台需求经 G6 试点验证后立项 |
| D-002 | 本地数据库路径与 PRAGMA | `%LOCALAPPDATA%\WarehouseWorkbench\data\warehouse.db`；每次连接执行 foreign_keys=ON、journal_mode=WAL、busy_timeout=5000；安装目录只放程序 | 2026-08-29 | 基线 §19.2/§35.3 | 不撤销（隐私与稳定性基线） |
| D-003 | 本地库加密（第一版） | 不启用 SQLCipher；本地目录使用 Windows 用户 ACL，敏感凭证走 DPAPI；隐私说明中明确"数据库文件被盗后可读"的边界 | 2026-08-29 | 基线 §19.2 | 完成独立打包验证后作为后续版本能力 |
| D-004 | 业务数据过云（加密中继） | 允许小程序事件以 AES-256-GCM 加密信封短暂经过云端中继（TTL 30 分钟），云端不解密；工作台 ACK 后删除密文 | 2026-08-29 | 基线 §21.6 | 仅在隐私评审要求收紧时缩短 TTL |
| D-005 | 失败/过期同步事件 | 过期事件进入 EXPIRED 不自动延长；REJECTED 保留错误码与原密文状态，不自动伪造成功 | 2026-08-29 | 基线 §32.2 | 无撤销计划 |
| D-006 | 实时状态通道 | SSE（Last-Event-ID 续传，失败后 30 秒轮询降级），第一版不引入 WebSocket | 2026-08-29 | 基线 §19 | 设备规模证明 SSE 不足时重评 |
| D-007 | 后台调度载体 | 托盘常驻 Agent（APScheduler + SQLAlchemyJobStore），非 Windows Service；被用户退出时如实标记离线与 MISSED，不伪报成功 | 2026-08-29 | 基线 §19/§19.1 | 客户环境验证服务化必要性后升级 |
| D-008 | 引擎接入方式 | 工作台仅经 WarehouseEngine Protocol + EngineProvider 组合根注入（WORKBENCH_ENGINE=fake\|local）；禁止 UI 出现 if fake；禁止导入 B 的内部模块 | 2026-08-29 | 基线 §30.4 | 无撤销计划 |
| D-009 | OpenAPI 唯一来源 | control-plane 导出 openapi.json 至 packages/contracts-schema，Web 类型由 openapi-typescript 生成；CI 重导出 diff 防漂移 | 2026-08-29 | 基线 §21.1/§41.3 | 无撤销计划 |
| D-010 | 商户类型与安装包 | 行业差异经 product_profile + 配置 + 许可证实现，一套代码；不做按商户定制 | 2026-08-29 | 基线 §2.3 | 无撤销计划 |
| D-011 | 商户业务数据云端边界 | 云端控制库不建 sku/movement/unit_cost 表；API 字段白名单、日志脱敏 | 2026-08-29 | 基线 §3.2/§35.2 | 不撤销（隐私红线） |
| D-012 | validate_dataset 签名 | 以主基线 §21.2 为准：validate_dataset(request, dataset) 双参数（确认 B 交接议题） | 2026-08-29 | m0-handover-b.md §6 | 无撤销计划 |
| D-013 | 密码哈希算法 | **Argon2id**（`argon2-cffi`），明文不落库 | 2026-08-30 | M2 控制平面认证（SECURITY.md 密钥与凭证基线） | 算法被证明不可逆或标准升级时重评 |
| D-014 | 令牌模型 | 无状态 **JWT Access（15min）** + 可轮换 **Refresh**（仅存 SHA-256 指纹）；每次请求回查 `session` 表使令牌可即时撤销 | 2026-08-30 | M2 认证闭环、可吊销需求 | 无撤销计划 |
| D-015 | Refresh 轮换与重放检测 | 一次性轮换（旧指纹移入 `previous_refresh_token_hash`）；`previous` 指纹再次出现即判定重放，吊销该账号**全部会话** | 2026-08-30 | 令牌泄露最小化损失原则 | 引入设备级令牌族后重评粒度 |
| D-016 | 许可证离线宽限期 | 默认 **7 天**（`LICENSE_OFFLINE_GRACE_DAYS` 可配，0 = 到期即限），**不落表**，按"到期日 + 宽限天数"推导；`ACTIVE`/`GRACE` 放行，`EXPIRED`/`REVOKED`/`MISSING` → 403 | 2026-08-30 | 主基线 §10.3 | 隐私/合规要求收紧时缩短 |
| D-017 | 仓储双实现 | 定义仓储协议；**内存实现**（测试注入 / 本地演示）+ **PostgreSQL 实现**（生产），组合根按 `CONTROL_PLANE_REPOSITORY` 选择；**生产环境拒绝 memory** | 2026-08-30 | 本地无 PG 可验证性、CI PG service 验证 | 无撤销计划 |
| D-018 | SSE 状态通道 | `EventHub`：单调事件 ID + 环形缓冲 + `Last-Event-ID` 续传 + 15s keepalive；客户端 30s 轮询降级（延续 D-006） | 2026-08-30 | M2 实时状态、弱网兜底 | 设备规模证明 SSE 不足时重评 |
| D-019 | 审计日志 | `audit_log` **只追加**（仓储不提供改/删入口）；`detail_json` 经白名单 `ALLOWED_DETAIL_KEYS` 过滤，不存密码/令牌/业务明细原文 | 2026-08-30 | 安全审计保留 180 天（§35.10） | 无撤销计划 |
| D-020 | dev token 下线 | M2 起 `Bearer merchant` / `Bearer developer` 字符串**不再被接受**，必须真实 JWT；缺失/无效 → 401，scope 不足 → 403 | 2026-08-30 | 关闭 M0 临时鉴权后门 | 无撤销计划 |

> M2 的 D-013~D-020 在 `feature/a-m2-control-plane-auth` 实现期间冻结，按基线 §18 要求变更须新建 ADR 并经 A、B 联合评审。

## 待 G1 前补充的决策（后续 ADR）

- 备份恢复的 RPO/RTO 演练标准（方向：RPO ≤ 24h、RTO ≤ 4h）。
- 开发者支持访问的限时授权与审计细则。
- 签名证书与密钥管理方案（M3 打包前冻结）。
