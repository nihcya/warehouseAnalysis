# Checklist

## 云端数据层与端点

- [x] 云端新增迁移覆盖 config_version、task、task_run、heartbeat、sync_envelope，往返测试通过
- [x] sync_envelope 有 event_id 唯一约束与 TTL 过期清理
- [x] heartbeat 以 device_id 保存最新投影（版本、Schema、Engine、待同步数）
- [x] POST /heartbeat / GET /config / GET /tasks / POST /tasks/pull / GET /sync/events/pull / POST /sync/ack 全部实装并替换 stub
- [x] /telemetry 与 /merchants 维持 stub（501）
- [x] 配置响应含版本号与 SHA-256 签名，客户端验签失败拒绝应用
- [x] Mock 小程序事件可注入云端（dev 工具），信封为密文
- [x] openapi.json 重导出与路由一致，契约测试通过

## 托盘 Agent 与同步链路

- [x] 关闭主窗口仅隐藏到托盘，心跳/同步继续；显式退出才停止
- [x] 心跳断网指数退避（上限 10 分钟），恢复后立即上报
- [x] 配置本地缓存可供离线启动读取
- [x] 任务由云端定义、本地执行，云端只收状态/耗时/版本/脱敏摘要
- [x] local-data 新增 0007_sync_config（sync_inbox/sync_outbox）
- [x] 同一 event_id 重复应用被唯一约束拦截，重复 ACK 幂等
- [x] 落库失败保留错误码与原密文，不伪造成功
- [x] 总览页显示待同步数

## 升级安全与打包

- [x] 启动迁移失败进入安全模式，数据库不被覆盖，可从备份恢复
- [x] 自动备份接入调度，升级前自动备份
- [x] PyInstaller --onedir 打包脚本产出可运行目录
- [x] Inno Setup 脚本支持安装/升级/卸载（数据保留在 %LOCALAPPDATA%）
- [x] 干净机验收清单已文档化

## 质量门槛

- [x] control-plane 与 workbench pytest 全绿
- [x] ruff check / mypy 无错误
- [x] pnpm lint / typecheck 通过
- [x] 端到端联调脚本跑通完整链路（登录→心跳→配置→同步→ACK→断网重试→幂等）
