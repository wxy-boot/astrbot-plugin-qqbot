# Sub2API QQ 助手（astrbot_plugin_sub2api_qqbot）

把 [Sub2API](https://github.com/wxy-boot) 站点的账号绑定、每日签到、消费达标抽奖搬到 QQ 群里。

- 与网页端**共用同一份数据**：网页签过 QQ 不能再签，QQ 抽过网页不能再抽。
- 签到、抽奖的**消费门槛各自独立**，由 `sub2api-qiandao`、`sub2api-choujiang` 两个服务自己的后台设置决定；
  插件不保存、不推断、不缓存门槛，每次都读取服务返回的 `eligibility`。
- 管理员改了门槛后，QQ 侧立即生效，不需要改插件配置或重启 AstrBot。

配套服务：

- 签到服务：<https://github.com/wxy-boot/sub2api-qiandao>
- 抽奖服务：<https://github.com/wxy-boot/sub2api-choujiang>

> 这两个服务需要已实现本文档约定的机器人接口（`/api/bot/checkin`、`/api/bot/draw`、`/api/bot/status`），
> 详见 [DEVELOPMENT.md](DEVELOPMENT.md) 第 8、9 节。

---

## 1. 命令

| 命令 | 别名 | 参数 | 作用 |
| --- | --- | --- | --- |
| `/绑定` | `bind` | 注册邮箱 | 绑定 QQ 与 Sub2API 账号 |
| `/签到` | `checkin`、`打卡` | 无 | 调用签到服务 |
| `/抽奖` | `draw`、`转盘` | 无 | 调用抽奖服务 |
| `/领测试额度` | `领额度`、`测试额度`、`trial` | 无 | 领取新人测试额度（注册早期限领一次，金额由签到服务后台决定） |
| `/查询` | `status`、`我的` | 无 | 查看绑定、余额、今日签到/抽奖/消费 |
| `/解绑` | `unbind` | 无 | 解除本地绑定 |
| `/帮助` | `help` | 无 | 显示命令说明 |

示例：

```text
用户：/绑定 alice@example.com
机器人：✅ 绑定成功
        账号：a***e@example.com
        余额：5.20 USD
        可用命令：/签到 /抽奖 /查询 /解绑 /帮助

用户：/查询
机器人：📋 今日状态
        账号：a***e@example.com
        余额：5.20 USD
        签到：已签到 +0.30 USD（08:12）
        抽奖：未抽奖
        签到门槛：未开启（不限消费）
        抽奖门槛：10.00 USD（今日消费 3.20，还差 6.80）
        抽奖机会：剩余 1 次
```

要点：

- 一个 QQ 只能绑定一个账号，一个账号也只能绑定一个 QQ。
- 群聊中邮箱默认脱敏（`a***e@example.com`），私聊显示完整邮箱。
- 同一 QQ 命令默认 2 秒冷却；`/帮助` 不受冷却限制。
- 所有回复都是纯文本。
- `/领测试额度` 的**开关、金额、注册天数全部在签到服务后台设置**（「奖励模式 → 测试额度」），
  插件不接受也不传递金额参数；只有注册未超过设置天数的账号能领，且每个账号限领一次，
  判定完全由签到服务完成。

示例：

```text
用户：/领测试额度
机器人：✅ 领取成功，+1.00 USD 测试额度已到账
        当前余额：6.20 USD

用户：/领测试额度
机器人：你已经领过测试额度了，每个账号限领一次

用户：/领测试额度（注册已超过 3 天）
机器人：账号注册已 10.20 天，超过可领取范围（注册 3.00 天内），无法领取测试额度
```

---

## 2. 环境要求

| 项 | 要求 |
| --- | --- |
| AstrBot | v4.x（开发验证于 v4.14 源码与 docstring 约定，部署建议 v4.28+） |
| 消息平台 | OneBot v11 / NapCat（`aiocqhttp`）或 QQ 官方机器人（`qq_official`）——插件只收发纯文本，默认不限制平台 |
| 依赖服务 | Sub2API、`sub2api-qiandao`、`sub2api-choujiang` |
| Python | 3.10+（插件无第三方依赖，`aiohttp` 由 AstrBot 提供） |

---

## 3. 安装

```bash
cd ~/astrbot/data/plugins
git clone https://github.com/wxy-boot/astrbot-plugin-qqbot.git astrbot_plugin_sub2api_qqbot
docker restart astrbot
```

> 目录名建议与 `metadata.yaml` 的 `name` 保持一致（`astrbot_plugin_sub2api_qqbot`），
> 否则升级/卸载时容易混淆。

检查加载情况：

```bash
docker logs --since 3m astrbot | grep -E "astrbot_plugin_sub2api_qqbot|Sub2API|Traceback"
```

日志出现 `Sub2API QQ 助手已就绪` 即表示插件加载成功。

---

## 4. 配置

在 AstrBot WebUI →「插件」→「Sub2API QQ 助手」里填写（对应 `_conf_schema.json`）：

| 键 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `sub2api_base_url` | string | `http://sub2api:8080/api/v1` | Sub2API 管理 API 地址，**必须含 `/api/v1`** |
| `sub2api_admin_email` | string | 空 | 专用管理员邮箱，仅用于登录管理 API |
| `sub2api_admin_password` | string | 空 | 专用管理员密码 |
| `allow_bind_admin` | bool | `false` | 是否允许绑定 Sub2API 管理员账号 |
| `qiandao_base_url` | string | `http://sub2api-qiandao:8787/checkin` | 签到服务地址，**含 `PUBLIC_BASE_PATH`** |
| `choujiang_base_url` | string | `http://sub2api-choujiang:8788/choujiang` | 抽奖服务地址，**含 `PUBLIC_BASE_PATH`** |
| `bot_shared_secret` | string | 空 | 与两个服务共享的机器人密钥（至少 16 位） |
| `qiandao_bot_secret` | string | 空 | 签到服务单独密钥；留空用 `bot_shared_secret` |
| `choujiang_bot_secret` | string | 空 | 抽奖服务单独密钥；留空用 `bot_shared_secret` |
| `request_timeout_seconds` | int | `30` | 单次上游请求总超时（5–120） |
| `command_cooldown_seconds` | int | `2` | 同一 QQ 命令冷却（0–60） |
| `platform_whitelist` | string | 空 | 允许响应的平台 ID，逗号分隔；**留空＝不限制**。NapCat 为 `aiocqhttp`，QQ 官方机器人为 `qq_official` |
| `allowed_group_ids` | string | 空 | 允许使用的群号（数字群号或官方机器人的 `group_openid`），逗号分隔；空表示所有群 |
| `currency_unit` | string | `USD` | 余额展示单位；签到/抽奖优先用服务返回值 |
| `mask_email_in_group` | bool | `true` | 群聊中隐藏邮箱中间部分 |

推荐配置：

```json
{
  "sub2api_base_url": "http://sub2api:8080/api/v1",
  "sub2api_admin_email": "bot@example.com",
  "sub2api_admin_password": "******",
  "qiandao_base_url": "http://sub2api-qiandao:8787/checkin",
  "choujiang_base_url": "http://sub2api-choujiang:8788/choujiang",
  "bot_shared_secret": "******",
  "platform_whitelist": "",
  "allowed_group_ids": ""
}
```

说明：

- `platform_whitelist` 留空时任何平台都能触发命令（NapCat 与 QQ 官方机器人开箱可用）；
  若这个 AstrBot 同时还接了 Telegram 等平台，填 `aiocqhttp,qq_official` 即可收窄范围。
- 插件配置里**没有**「消费门槛金额」：门槛只在两个服务各自的后台设置。
- 插件配置里**也没有**「测试额度开关/金额/注册天数」：这些全在签到服务后台
  （「奖励模式 → 测试额度」）设置，`qiandao_base_url` 与签到共用同一个服务地址。
- `sub2api_admin_email` 必须是 `admin` 角色的**专用账号**，否则 `/admin/users` 会返回 403；不要用主管理员。
- 配置不合法（URL 不是 http(s)、超时越界、密钥短于 16 位等）时插件会加载失败并在日志中给出明确原因；
  未填写管理员凭据或机器人密钥时插件仍可加载，对应命令会提示「未配置」。

### 网络连接

| AstrBot 位置 | Sub2API | 签到 / 抽奖 |
| --- | --- | --- |
| 容器内（同一 docker 网络） | `http://sub2api:8080` | 用容器服务名 |
| 容器内，服务在宿主机 | `http://host.docker.internal:8080`（Linux 另加 `extra_hosts`） | 同理 |
| 裸机运行 | `http://127.0.0.1:8080` | `http://127.0.0.1:8787` |

### 服务端准备

三个服务都要配置同一个 `BOT_SHARED_SECRET`（至少 32 位随机字符串）：

```bash
openssl rand -hex 32
```

```dotenv
# 签到服务 / 抽奖服务的 .env
BOT_SHARED_SECRET=xxxx
```

重启服务后自测：

```bash
curl -s -X POST http://127.0.0.1:8787/checkin/api/bot/checkin \
  -H "X-Bot-Secret: $BOT_SHARED_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"userId":"42","userName":"alice"}'
```

---

## 5. 绑定数据（`state.json`）

- 位置：插件目录下的 `state.json`（`data/plugins/astrbot_plugin_sub2api_qqbot/state.json`），已被 `.gitignore` 忽略。
- 只保存 `QQ → {uid, email, name, bound_at}`；余额、签到/抽奖记录都以服务端为准。
- 写入使用「临时文件 + fsync + 原子替换」，文件权限尽量为 `0600`。
- **备份**：升级前先复制该文件。

  ```bash
  cp state.json ~/state-backup-$(date +%F).json
  ```

- **恢复 / 手动解绑**：先停 AstrBot，再覆盖或编辑 `state.json`，最后启动。
- **损坏处理**：JSON 解析失败时插件会把文件重命名为 `state.json.corrupt-<时间戳>`，
  然后进入只读保护状态：`/帮助` 仍可用，其余命令回复「绑定数据损坏，请联系管理员恢复备份」。
  插件**不会**自动重建空文件，避免悄悄丢失全部绑定。
- **版本**：`version` 高于插件支持版本时拒绝写入，并提示升级插件。

---

## 6. 安全建议

邮箱绑定**无法证明邮箱归属**：知道他人邮箱的人可以抢先绑定。因此：

- 只在私密、可控的群使用；可用 `allowed_group_ids` 限制群号。
- 默认禁止绑定管理员账号（`allow_bind_admin=false`）。
- 不要公开宣传 `/绑定` 命令，不要把机器人拉进公开群。
- AstrBot WebUI 不要裸奔公网：至少改默认密码，并加反向代理 Basic Auth / IP 白名单。
- NapCat WebUI（6099）只对内网开放。
- 两个服务的机器人接口不要暴露公网，仅在容器网络可达；如必须暴露，在反向代理再加一层限制。
- 专用 Sub2API 管理员账号只授予必要权限；定期轮换 `BOT_SHARED_SECRET`（轮换时同步更新三处配置）。
- 插件不会在日志和群消息中输出管理员密码、Token 或机器人密钥；日志中的 QQ 只保留后 4 位、邮箱只保留脱敏形式。

后续增强（绑定码、邮件验证）见 [DEVELOPMENT.md](DEVELOPMENT.md) 第 10.3 节，本期未实现。

---

## 7. 常见问题

| 现象 | 排查方向 |
| --- | --- |
| `/帮助` 没反应 | 见下方「机器人不回复命令」；插件未加载或被停用时任何指令都不会有反应 |
| 机器人只回大模型的话（一段 AI 回答），不回插件文案 | 说明消息没被插件的指令过滤器接住：`/帮助` 看 AstrBot 日志里的 `收到命令 /帮助`；没有这行就按下一节逐项排查 |
| `/绑定` 提示未找到账号 | 邮箱是否与 Sub2API 注册邮箱完全一致；账号是否被删除 |
| `/绑定` 提示管理员凭据未配置 | 填写 `sub2api_admin_email` / `sub2api_admin_password` |
| `/绑定` 提示站点管理员尚未完成合规确认 | 用管理员登录 Sub2API 面板完成合规确认（HTTP 423） |
| `/签到` 提示未配置机器人密钥 | 配置 `bot_shared_secret` 或对应服务的专用密钥 |
| `/签到` 提示未配置或密钥错误 | 三个服务的 `BOT_SHARED_SECRET` 与插件配置是否一致（HTTP 401） |
| `/签到` 提示今日消费未达到门槛 | 这是签到服务自己的门槛设置，去签到服务后台调整或让用户继续消费 |
| `/领测试额度` 提示活动暂未开启 | 签到服务后台「奖励模式 → 测试额度」里打开开关 |
| `/领测试额度` 提示注册超过可领取范围 | 该账号注册时间超过后台设置的天数（默认 3 天），属正常拦截 |
| `/领测试额度` 提示接口尚未部署 | 签到服务版本过旧，升级到带 `/api/bot/trial-credit` 的版本 |
| `/领测试额度` 提示无法确认注册时间 | 签到服务的 `SUB2API_USER_INFO_PATH_TEMPLATE` / `SUB2API_USER_CREATED_AT_FIELD` 未配置正确 |
| `/查询` 某行显示「查询失败」 | 对应服务地址/网络不可达，或该服务关闭了机器人接口；其他行不受影响 |
| 提示操作太频繁 | `command_cooldown_seconds` 冷却中，稍后重试 |
| 写完请求超时/断网 | 结果未知，先用 `/查询` 确认，**不要**重复发送（避免重复加余额） |

### 机器人不回复命令（只回大模型 / 完全不回）

AstrBot 的分发顺序是：**插件指令 → 大模型**。所以“机器人回了一段 AI 文案”＝指令没被接住，
而不是模型/网络的问题。按下面顺序看日志即可定位：

```bash
# 1) 插件加载与配置策略（应能看到「已就绪」「平台限制=…；群白名单=…」）
docker logs --since 10m astrbot | grep -E "Sub2API QQ 助手|astrbot_plugin_sub2api_qqbot"

# 2) 发一条 /帮助 后，看插件有没有收到事件
docker logs --since 2m astrbot | grep -E "收到命令|已忽略|Traceback"
```

| 日志/现象 | 结论与处理 |
| --- | --- |
| 连「已就绪」都没有 | 插件没加载：目录名需为 `astrbot_plugin_sub2api_qqbot`；WebUI 插件页是否显示「已启用」；看有无 `Traceback` |
| `已忽略平台的命令：platform='…'` | 平台被 `platform_whitelist` 挡住，按日志里的值改配置或留空 |
| `已忽略群 '…' 的命令` | 群号不在 `allowed_group_ids`：注意官方机器人的群号是 `group_openid`（形如 `A1B2…`），不是数字群号 |
| 有「已就绪」但没有「收到命令」 | 平台适配器没把消息给插件：确认 NapCat 反向 WS 已连接，或官方机器人已在群里被 @ |

补充：**@机器人后可以不带 `/`**（AstrBot 会把第一个 @机器人 从消息里去掉，`@二狗 帮助` 等价 `/帮助`）；
但手打的 `@二狗` 只是普通文字时不会被识别，请用真正的提及，或直接发 `/帮助`。

---

## 8. 升级

```bash
cd ~/astrbot/data/plugins/astrbot_plugin_sub2api_qqbot
cp state.json ~/state-backup-$(date +%F).json
git pull
docker restart astrbot
```

> v0.2.0 起新增 `/领测试额度`，需要签到服务同步升级到带 `/api/bot/trial-credit` 的版本。
> 只升级插件时该命令会提示「签到服务还没有提供测试额度接口」，其余命令不受影响。

---

## 9. 开发

目录结构：

```text
astrbot-plugin-qqbot/
├─ main.py               # AstrBot 适配层：命令、过滤、冷却、异常兜底
├─ qbot/
│  ├─ app.py             # 命令业务逻辑（不依赖 AstrBot）
│  ├─ client.py          # Sub2API / 机器人接口 HTTP 客户端
│  ├─ config.py          # 配置解析与校验
│  ├─ formatting.py      # 全部用户可见文案与金额/时间格式化
│  ├─ ratelimit.py       # 命令冷却
│  ├─ state.py           # state.json 原子读写与损坏保护
│  └─ log.py             # logger 适配（无 AstrBot 时回退标准库）
├─ _conf_schema.json     # WebUI 配置 schema
├─ metadata.yaml         # 插件元信息
├─ requirements.txt      # 无第三方依赖
├─ pytest.ini
├─ pyproject.toml        # ruff 静态检查配置
├─ tests/                # 单元测试 + 契约测试 + 集成测试
├─ DEVELOPMENT.md        # 设计文档（上游接口契约、数据流）
└─ README.md
```

本地测试（`aiohttp` / `pytest` / `ruff` 仅测试时需要）：

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -U pytest pytest-asyncio aiohttp ruff   # Windows
# .venv/bin/python -m pip install -U pytest pytest-asyncio aiohttp ruff     # Linux/macOS

.venv/Scripts/python -m pytest
.venv/Scripts/python -m compileall -q main.py qbot tests
.venv/Scripts/ruff check main.py qbot tests
```

测试覆盖：配置校验、状态原子写入与损坏保护、文案格式化、上游接口契约（请求头/请求体/重试策略/错误映射）、
命令业务逻辑（绑定/签到/抽奖/领测试额度/查询/解绑、门槛开与关、注册时间窗口内与超期、校验失败不放行）、限流、AstrBot 适配层，
以及基于本地 mock 服务的端到端流程（含「网页已签到 QQ 不能再签」「测试额度每账号只发一次」）。

---

## 10. 许可

见 [LICENSE](LICENSE)。
