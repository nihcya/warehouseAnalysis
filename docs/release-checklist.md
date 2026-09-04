# 发布验收清单（干净机全流程）

适用范围：M3 安装包发布（spec `deliver-m3-agent-sync-release`「安装包」需求及关联 Scenario）。
验收机要求：全新 Windows 10/11 x64 虚拟机（或还原到干净快照），**不预装 Python 与本仓库任何依赖**。

> 用法：每轮发布按节逐项勾选；任一项失败即视为验收不通过，附截图/产物哈希回传归档。
> 产物版本以 `apps/workbench-desktop/pyproject.toml` 为准，哈希以 `dist/SHA256SUMS.txt` 为准。

## 0. 前置准备

- [ ] 干净机已还原快照（无 Python、无旧版本工作台、无残留 `%LOCALAPPDATA%\WarehouseWorkbench`）
- [ ] 安装包来自 `dist/WarehouseWorkbench-Setup-<版本>.exe`，`dist/SHA256SUMS.txt` 与产物一并取得
- [ ] 哈希核验一致（PowerShell）：`Get-FileHash .\WarehouseWorkbench-Setup-<版本>.exe -Algorithm SHA256`
- [ ] 已阅读 `dist/RELEASE_NOTES.txt`：确认签名状态（未签名时 SmartScreen 拦截属预期行为）
- [ ] 控制平面（`services/control-plane`）在可达地址运行，并准备了测试账号与 Mock 小程序事件（断网/同步场景除外）

## 1. 干净机安装（Scenario：干净机安装）

- [ ] 双击安装包可正常启动安装向导，界面为简体中文
- [ ] 许可/选项页可正常前进，默认安装到 `C:\Program Files\WarehouseWorkbench`（Program Files，需 UAC 提权）
- [ ] 桌面快捷方式「仓库分析工作台」按勾选项创建；开始菜单含主程序与卸载入口
- [ ] 安装完成无报错；安装目录含 `WarehouseWorkbench.exe` 与 `_internal\`（含 `local-data\alembic\versions\` 迁移脚本）
- [ ] 控制面板「应用」列表出现「仓库分析工作台 <版本>」，卸载入口可用
- [ ] 未签名产物首次启动出现 SmartScreen 提示时，可选择「更多信息 → 仍要运行」正常进入（预期行为，已在发布说明标注）

## 2. 首次启动与数据目录

- [ ] 启动后主窗口正常显示，无 Python/控制台报错弹窗
- [ ] 首启自动建库：`%LOCALAPPDATA%\WarehouseWorkbench\data\warehouse.db` 生成（Alembic 迁移到 head）
- [ ] 同目录出现 `backups\`、`reports\` 兄弟目录（首次使用后创建）
- [ ] 安装目录（Program Files）内**不产生**数据库等用户数据（只存程序）
- [ ] 登录控制平面成功后设备注册、状态显示在线（离线环境则显示离线且本地操作不受阻断）

## 3. 核心功能冒烟（Scenario：干净机安装——导入、分析、备份）

- [ ] 导入库存事件 CSV：校验错误逐行可读、成功行落库
- [ ] 运行分析：结果落库并在 UI 展示（含基准指标或 `BENCHMARK_UNAVAILABLE` 非阻断告警）
- [ ] 手动备份：`backups\` 出现备份文件，备份记录状态 VERIFIED、SHA-256 与文件一致
- [ ] 报告导出：`reports\` 下生成报告文件且内容可读

## 4. 托盘常驻 Agent（Scenario：关窗常驻）

- [ ] 关闭主窗口：窗口隐藏、托盘图标存活、心跳与同步继续
- [ ] 托盘菜单「显示」可重新打开窗口；「退出工作台」显式退出后进程全部结束（任务管理器无残留 `WarehouseWorkbench.exe`）

## 5. 断网与恢复（Scenario：断网退避 / 断网重试）

- [ ] 断网后：心跳按指数退避重试（上限 10 分钟），UI 状态为离线，本地操作不受阻断
- [ ] 断网期间小程序事件信封保持 PENDING 不丢失；恢复网络后自动补拉并落库、ACK
- [ ] 恢复网络后首次心跳立即发送（无需等到下一个退避周期）
- [ ] 重复下发同一 `event_id`：本地唯一约束拦截，重复 ACK 幂等成功，无重复数据
- [ ] 下发签名不匹配的配置：客户端拒绝应用、保留旧配置并告警（Scenario：验签失败）

## 6. 升级覆盖安装

- [ ] 在含用户数据的旧版本上直接运行新版本安装包：提示关闭运行中的工作台后覆盖安装完成
- [ ] 升级后首启自动执行数据库迁移；用户数据（数据/备份/报告/登录凭据）完整保留、可继续使用
- [ ] 升级前自动备份已生成（可回滚）；版本号在控制面板「应用」列表更新为新版本
- [ ] **升级迁移失败演练**（构造迁移报错库或低版本库）：启动进入安全模式页，数据库保持迁移前状态，可从升级前备份一键回滚（Scenario：升级迁移失败）

> 版本回滚入口说明：升级（迁移）前应用自动经 `app.application.upgrade_safety.run_pre_upgrade_backup`
> 对旧库做一次 AUTO 类型全量备份（备份文件与记录登记于
> `%LOCALAPPDATA%\WarehouseWorkbench\backups\`，结果消息含当前应用版本标记）；
> 升级后如需回退到旧版本应用：退出工作台（托盘 → 退出）→ 卸载新版本并安装旧版本安装包 →
> 启动旧版本；若旧版本迁移报 schema 不兼容，在安全模式页选择「从备份恢复」用上述升级前
> 备份回滚数据后重启即可（迁移失败时旧库文件零改动，见安全模式流程）。

## 7. 卸载（Scenario：干净机安装——卸载）

- [ ] 卸载向导可正常完成，Program Files 安装目录被清空/移除
- [ ] 桌面与开始菜单快捷方式同步移除
- [ ] 卸载完成弹窗明确提示：用户数据保留在 `%LOCALAPPDATA%\WarehouseWorkbench`，如需彻底清理请手动删除
- [ ] 卸载后 `%LOCALAPPDATA%\WarehouseWorkbench`（数据库/备份/报告/凭据）**仍然保留**
- [ ] 重装同版本后旧数据可继续使用（保留策略生效）

## 8. 诊断包收集（配合排障）

- [ ] 收集 `%LOCALAPPDATA%\WarehouseWorkbench\` 全目录（`data\`、`backups\`、`reports\`、`auth.json`）
- [ ] 附带版本信息：安装包文件名版本、控制面板显示版本、`alembic_version`（`warehouse.db` 内）
- [ ] 附带 `dist\SHA256SUMS.txt` 与 `dist\RELEASE_NOTES.txt`，标注验收机系统版本与复现时间
- [ ] 敏感提醒：诊断包含登录凭据文件时按内部规范脱敏/加密传输

## 9. 结果归档

- [ ] 全部勾项截图/录屏归档，记录产物哈希与验收机快照编号
- [ ] 失败项逐条登记（步骤、现象、日志/截图），阻断项修复后整轮重跑
