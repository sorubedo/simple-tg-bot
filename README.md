# bottg

一个专注于管理单个 Telegram 超级群组（话题群组）的机器人骨架。

设计约束：

- **无状态**：不使用数据库、会话文件或 PTB persistence，业务状态不留在进程内。
- **功能解耦**：Telegram 连接/生命周期与业务功能分离；后续业务动作由配置中的外部程序/脚本承担。
- **配置驱动**：唯一配置文件使用 JSON5，支持注释、尾逗号和未加引号的键。
- **当前里程碑只做启动链路**：加载配置、连接 Telegram、校验目标群组、进入长轮询主循环。不发送消息，不处理消息，不执行外部程序。

---

## 1. 技术选型

### `python-telegram-bot==22.8`

选择它作为 Telegram Bot API 客户端，原因：

1. **与项目的 Python 3.15.2 兼容**。截至搭建时，`aiogram 3.31.0` 的 `requires-python` 是 `>=3.10,<3.15`，无法安装到本项目的 3.15.2 环境；`python-telegram-bot 22.8` 要求 `>=3.10`，没有 3.15 上限。
2. 成熟、维护活跃，原生 asyncio，直接覆盖本阶段需要的 `getUpdates` 长轮询、更新队列、生命周期和优雅关闭。
3. 后续可以直接使用它的 handler、filter、chat/topic 过滤能力，不需要先自己搭一套更新分发基础设施。
4. 相比自己用 `httpx` 调 Bot API，省去维护 `offset`、重试、超时、取消和关闭顺序的成本；相比 Telethon/Pyrogram，它使用 bot token 而不是用户账号，也不需要 MTProto 的 `api_id` / `api_hash` 和会话文件。

### `json5==0.17.3`

只负责解析 JSON5。配置文件里可以写注释和尾逗号，便于说明后续外部程序的配置。

### 开发依赖

- `pytest`：配置解析和目标群组校验的单元测试。

### 为什么不用其他方案

- **aiogram**：API 设计很好，但当前版本的 Python 上限与项目固定的 3.15.2 冲突。如果以后降级 Python 到 3.14 或以下，可以重新评估。
- **Telethon / Pyrogram**：它们是 MTProto 客户端，通常以用户身份登录并依赖 `.session` 文件，与“bot token + 无状态”的定位不符，也超出当前需求。
- **裸 `httpx`**：依赖最少，但长轮询的 offset、错误重试、生命周期和信号处理都要自己实现，现阶段不值得。

---

## 2. 目录结构

```text
.
├── config.example.json5     # 可提交的配置示例
├── config.json5             # 本地实际配置（已被 .gitignore 忽略）
├── pyproject.toml
├── src/bottg/
│   ├── __init__.py          # 版本号
│   ├── __main__.py          # python -m bottg
│   ├── cli.py               # 参数、日志、退出码
│   ├── config.py            # JSON5 加载与强校验
│   ├── bot.py               # PTB 生命周期、群组校验、长轮询
│   └── errors.py            # 配置/启动异常
└── tests/
    ├── test_bot.py
    └── test_config.py
```

边界约定：

- `config.py` 只产生不可变的配置对象，不接触 Telegram。
- `bot.py` 只负责 Telegram 连接、目标校验和主循环，不包含业务动作。
- `programs` 配置段目前只被解析并保留为原始映射，**不会执行**；后续外部程序调度器应作为独立模块接入。

---

## 3. 安装与启动

```bash
uv sync

cp config.example.json5 config.json5
# 编辑 config.json5，填入目标超级群组 ID

export BOTTG_TOKEN='123456789:AA...'
uv run bottg
```

也可以指定配置文件：

```bash
uv run bottg --config /path/to/config.json5
# 或
BOTTG_CONFIG=/path/to/config.json5 uv run bottg
```

`BOTTG_TOKEN` 的优先级高于 `telegram.token`。推荐只使用环境变量，避免 token 进入配置文件或版本库。

### 准备目标群组

1. 把机器人加入目标群组；私有超级群组至少需要机器人是成员。
2. 获取群组的数字 ID。先停止正在运行的 bottg，然后在群里发一条消息，再请求：

   ```bash
   curl -s "https://api.telegram.org/bot${BOTTG_TOKEN}/getUpdates" | python3 -m json.tool
   ```

   在返回的 `result[].message.chat.id` 中找到 `-100...` 形式的 ID，填入 `telegram.group_id`。
3. 如果要用话题功能，在群组设置中开启 **Topics**。启动时 bot 会检查群组类型是 `supergroup`，并报告 `is_forum`；没有开启 Topics 时只警告，不阻止启动。
4. 长轮询和 webhook 互斥。PTB 在开始轮询的 bootstrap 阶段会自动调用
   `deleteWebhook`，因此 bottg 会接管该 bot 的更新获取方式；如果这个 bot
   同时被其他 webhook 服务使用，请为 bottg 使用独立的 bot token。

---

## 4. 配置说明

```json5
{
  telegram: {
    token: "",                    // 可留空，使用 BOTTG_TOKEN
    group_id: -1001234567890,     // 必填，负数
  },
  polling: {
    timeout_seconds: 30,          // 长轮询超时，>= 1
    poll_interval: 0.0,           // 两次轮询的额外间隔，>= 0
    drop_pending_updates: false,  // 启动时是否丢弃积压更新
    allowed_updates: null,        // null=全部；或 ["message", ...]
  },
  runtime: {
    log_level: "INFO",
  },
  programs: {},                   // 预留，当前不执行
}
```

校验规则：

- 未知键会直接报配置错误，防止拼写错误被静默忽略。
- `telegram.group_id` 必须是负整数。
- token 必须符合 `<bot_id>:<secret>` 的基本形式。
- `runtime.log_level` 接受 `DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL` 等 logging 级别名。
- `programs` 必须是对象，但内部结构暂不解释。

---

## 5. 启动流程

1. 解析 CLI 参数和 JSON5 配置；失败时退出码为 `2`。
2. 创建 PTB `Application`，不注册任何 handler。
3. 初始化 bot，调用 `getChat(group_id)`。
4. 校验返回的 chat 必须是 `supergroup`；否则启动失败。若 `is_forum` 为假，记录警告。
5. 启动应用更新处理器和 `getUpdates` 长轮询。
6. 向 **stdout** 输出唯一一行初始化成功信息，例如：

   ```text
   bottg 0.1.0 initialized | target supergroup 'Ops' (id=-1001234567890 @ops_group) | topics=on | long polling started (timeout=30s)
   ```

   运行日志全部写到 **stderr**。
7. 等待 `SIGINT` / `SIGTERM`，然后停止 updater、停止 application、关闭 HTTP 客户端。

退出码：

| 退出码 | 含义 |
| --- | --- |
| `0` | 正常停止 |
| `1` | 启动失败、Telegram API 错误或未预期异常 |
| `2` | 配置文件缺失、JSON5 语法错误或校验失败 |
| `130` | 被中断 |

### 关于“与指定群组建立长连接”

这里的长连接指 Bot API 的 `getUpdates` 长轮询，不是 MTProto 的持久 TCP 连接，也不是 webhook。

`getUpdates` 是 **bot 级别**的，不能只订阅某一个 chat。bottg 在启动时校验配置的群组，并进入长轮询；后续消息分发层需要按 `update.effective_chat.id == telegram.group_id` 过滤。当前版本没有任何 handler，也没有消息发送逻辑，因此收到的更新不会产生动作。

---

## 6. 当前版本明确不做的事

- 发送/编辑/删除消息
- 注册消息、命令、回调或话题事件 handler
- 调用外部程序/脚本
- 保存任何状态、会话或更新 offset 到磁盘
- Webhook 模式
- 限流、重试策略和业务幂等性设计

`getUpdates` 的 offset 由 PTB 的 `Updater` 在内存中推进，进程重启后不会保留。这是“无状态”定位的有意取舍；未来引入外部程序时，需要明确失败重试和“至少一次/至多一次”的语义。

---

## 7. 开发检查

```bash
uv run pytest
uv run bottg --version
uv run python -m bottg --help
```

测试不需要真实 token，也不会访问 Telegram。
