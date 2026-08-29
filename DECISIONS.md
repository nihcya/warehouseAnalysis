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

## 待 G1 前补充的决策（后续 ADR）

- 备份恢复的 RPO/RTO 演练标准（方向：RPO ≤ 24h、RTO ≤ 4h）。
- 开发者支持访问的限时授权与审计细则。
- 签名证书与密钥管理方案（M3 打包前冻结）。
