# SECURITY.md

## 报告漏洞

发现安全漏洞请勿公开 Issue，联系开发者私聊或邮箱，按 P0 流程处理。

## 安全基线（M0 冻结方向，细则随各阶段实装）

- 商户密码：Argon2id 哈希；开发者生产账号必须启用 MFA（M2 实装）。
- Token：Access 15 分钟；Refresh 轮换且只存哈希/指纹，可撤销（M2 实装）。
- 商户范围由后端 Token 的 tenant_id 决定，禁止信任前端传入 merchant_id。
- 开发者接口必须 `developer:*` Scope；商户接口必须 `require_tenant_access`。
- CORS 使用明确域名白名单，禁止 `*`；生产仅 HTTPS。
- 本地敏感凭证使用 Windows DPAPI；密钥不写入代码、配置文件或日志。
- 技术日志字段白名单脱敏；同步信封 TTL 内删除密文。
- 安装包 Windows 代码签名 + SHA-256 校验（M3 实装）。

## 隐私边界

云端控制库不保存商户完整 SKU、库存、成本与采购明细；小程序事件仅以加密信封短期中继。
第一版本地 SQLite 不启用文件级加密（见 DECISIONS.md D-003），隐私说明中如实披露。
