# muse-auto-register

muse.ai 全自动注册 + 邀请码兑换工具。纯 HTTP 实现，不依赖浏览器。

**邮箱能力由 [chat-tempmail.com](https://chat-tempmail.com) 提供** —— 临时邮箱自动生成、验证码邮件自动收取，全程无需人工干预。

## 为什么接 chat-tempmail.com

- **全自动闭环**：API 创建邮箱 → muse.ai 发验证码 → 轮询收件箱 → 正则提取验证码 → 完成注册，一条命令跑完。
- **多域名可选**：`GET /api/email/domains` 返回多个可用域名，降低单域名被风控的概率。
- **可分享收件箱**：`POST /api/emails/{id}/share` 生成公开只读链接，方便把收件箱甩给团队/客户看信。
- **Webhook 推送**：新邮件即时 POST 到你的服务器，验证码可以做到秒级到达（见下方说明）。

👉 先去 <https://chat-tempmail.com> 注册，在 Profile 页创建 API Key。

## 快速开始

```bash
pip install -r requirements.txt
cp config.example.json config.json   # 填入 tempmail_api_key 与可选代理
python example_register.py
```

`config.json` 关键字段：

| 字段 | 说明 |
| --- | --- |
| `tempmail_api_key` | chat-tempmail.com 的 API Key（必填，也可用环境变量 `TEMPMAIL_API_KEY`） |
| `tempmail_domain` | 指定邮箱域名；留空自动取第一个可用域名 |
| `tempmail_expiry` | 邮箱有效期 ms：3600000=1小时 / 86400000=1天 / 259200000=3天 / 0=永久 |
| `otp_timeout` | 等验证码的最长秒数，默认 180 |
| `otp_sender_contains` / `otp_subject_contains` | 过滤发件人/主题，防止抓错邮件 |
| `invite_code` | 要兑换的邀请码，留空则只注册不兑换 |
| `proxy` | HTTP(S) 代理，注册与收信共用 |

## 目录结构

```
musepy/
  tempmail.py          chat-tempmail.com API 封装（域名/建箱/收信/验证码提取/分享）
  client.py            muse.ai 注册 + 兑换的完整协议客户端
  falco.py             行为埋点上报（可选）
  caa.py               注册漏斗事件上报（可选）
example_register.py    端到端示例：建箱 → 注册 → 收码 → 兑换
config.example.json    配置模板
```

## 全自动流程

```
chat-tempmail.com API                muse.ai
─────────────────────                ─────────────────────────────
POST /api/emails/generate   ──►  GET  /                            拿初始 cookie
                            ──►  POST /api/auth/native/restart     → 201 {csrf_token}
                            ──►  POST /api/auth/native/send-otp    → 201
GET  /api/emails/{id}       ◄──  (验证码邮件到达)
GET  /api/emails/{id}/{msg} ──►  正则提取 4-8 位验证码
                            ──►  POST /api/auth/native/confirm-otp → 200
                            ──►  POST /api/auth/native/register-completion → 200
                            ──►  POST /api/hatch/accept-tos        → 200（激活会话，必须）
                            ──►  GET  /api/hatch/invite            → 200 表示账号有效
                            ──►  POST /api/hatch/vm/wake           → 200（必须，否则兑换误报 invalid）
                            ──►  POST /api/hatch/stateful-sharing  → 200
                            ──►  POST /api/hatch/invite-code/redeem→ 200 {redemptionStatus:"redeemed"}
                            ──►  POST /api/hatch/invite            → 200 {code}（刮出自己的码）
```

## 验证码提取规则

`musepy/tempmail.py` 的 `extract_otp` 挂两级正则，按优先级尝试：

1. 带关键词上下文的 4-8 位数字（`code` / `verify` / `otp` / `認証` / `인증` 等中英日韩关键词）
2. 兜底：正文第一组 4-8 位数字

验证码邮件迟迟没到时，登录 <https://chat-tempmail.com> 可直接看该邮箱的网页版收件箱。

## Webhook 秒级收码（可选）

轮询之外，chat-tempmail.com 还支持 Webhook 推送：通过
`POST /api/webhook` 配置（`url` + `enabled`），新邮件会立刻 POST 到你的服务器，
Header 为 `X-Webhook-Event: new_message`，Body 含 `content`/`html`/`subject`，
可直接复用 `extract_otp` 提取验证码。适合批量并发注册的场景。

## 几个容易踩的坑（muse.ai 协议）

### 1. `confirm-otp` 必须带 `supports_muse_link_transparency: true`

```json
{"otp_code":"123456","remember_device":true,"supports_muse_link_transparency":true}
```

### 2. `register-completion` 的姓名字段语义

```json
{
  "date_of_birth": "1993-11-14",
  "first_name": "三郎",
  "last_name": "近藤",
  "tos_content_version": "ntm_create_account:default_v1_meta_account_v3+docs_v2",
  "remember_device": true
}
```

`first_name` 是名、`last_name` 是姓，与直觉相反。生日早于约 18 年前会返回 403——同一会话同一份姓名，只把生日从 2013 改成 1993 就能从 403 变 200。

### 3. `restart` 的 body 不是空对象

```json
{"resume": false}
```

### 4. `accept-tos` 是会话激活开关

注册完成后 `GET /api/hatch/invite` 会是 403。必须先调 `accept-tos`，且 `disclosure_text` 要填完整日文文案（不是随便一个字符串），之后 invite 才返回 200。

### 5. `vm/wake` 缺失会让兑换误报

不调 `vm/wake` 直接兑换，即使是有效码也会返回：

```json
{"success":false,"reason":"invalid","detail":"この招待コードは存在しません"}
```

需要先从已登录页面提取 `vmId`：

```python
re.search(r'vmId[\\"]*:[\\"]*([0-9a-fA-F-]{36})', html)
```

### 6. 兑换失败原因的含义

| reason | 含义 |
|---|---|
| `redeemed` | 成功 |
| `already_redeemed` | 本账号已经兑换过一个码 |
| `used_up` | 该码达到 30 次使用上限 |
| `invalid` | 码不存在，或没调 `vm/wake` |

## Cookie

注册成功后 `hatch_sess` 由 `register-completion` 的响应头下发（约 886 字符），这是后续所有 hatch 接口的凭证。`hatch_vml` 在注册阶段一直是被清空状态（`Max-Age=0`），不属于必需项。

## 埋点说明

站点前端会上报两类遥测：

- **falco**（`POST /api/falco`）—— 性能指标与业务交互事件，`machineId` 固定为 `not_set`
- **caa-events**（`POST /api/auth/caa-events`）—— 注册漏斗，通过 `navigator.sendBeacon` 逐步骤上报，同一 `waterfall_id` 贯穿全程

两者都不参与注册判定，缺省不影响流程能跑通。`musepy/falco.py` 与 `musepy/caa.py` 保留了完整实现，可通过配置关闭。

## 说明

本项目用于协议研究与学习。实际使用时请遵守目标站点与 chat-tempmail.com 的服务条款及当地法律法规。
