# 登录会话与 Refresh Token

## 当前设计

- Access token 有效期：1 小时。
- Refresh token 有效期：14 天。
- Access token payload 包含 `sub`、`sid`、`jti`、`type=access`。
- Refresh token payload 包含 `sub`、`sid`、`jti`、`type=refresh`。
- Refresh token 明文只返回客户端，服务端只保存 SHA-256 哈希。
- 会话表：`user_sessions`。

## 存储位置

前端：
- `pm.accessToken`
- `pm.refreshToken`
- `pm.deviceId`

后端：
- `user_sessions.refresh_token_hash`
- `user_sessions.jti`
- 设备、浏览器、IP、user_agent 等审计字段。

## 校验流程

1. 普通 API 请求带 access token。
2. 后端解 JWT，校验 `type=access`。
3. 如果 access token 带 `sid`，继续查询 `user_sessions`：
   - 会话存在
   - 未撤销
   - 未过期
4. 前端遇到 401 后调用 `/api/auth/refresh`。
5. refresh 成功后重放原请求；失败则清理本地 token 并跳转登录页。

## 单端登录规则

当前规则：
- 同一账号、同一 `client_type` 只保留一个有效会话。
- `api` 类型允许多会话，便于脚本/集成调用。

典型 `client_type` 预留：
- `web`
- `android`
- `ios`
- `mini_program`
- `api`

## 撤销场景

以下操作会撤销用户已有会话：
- 修改密码
- 管理员重置密码
- 停用账号
- 删除账号
- 同账号同端重复登录
- 主动退出登录
- 退出所有设备

## 后续优化

- 增加“我的登录设备”页面。
- 支持管理员强制下线指定会话。
- 定期清理过期/撤销会话。
- 生产环境启动时强校验 `JWT_SECRET_KEY`，禁止默认弱密钥。
