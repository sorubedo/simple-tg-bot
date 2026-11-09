# bottg

一个专注于管理单个 Telegram 超级群组（话题群组）的机器人骨架。

设计约束：

- **无状态**：不使用数据库、会话文件或 PTB persistence，业务状态不留在进程内。
- **功能解耦**：Telegram 连接/生命周期与业务功能分离；后续业务动作由配置中的外部程序/脚本承担。
- **配置驱动**：唯一配置文件使用 JSON5，支持注释、尾逗号和未加引号的键。
- **当前里程碑只做启动链路与日志**：加载配置、连接 Telegram、校验目标群组、进入长轮询主循环，并把运行日志同步到目标群组的默认 `#General` 话题。不处理业务消息，不执行外部程序。

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
│   ├── logging_setup.py     # stdout 与 Telegram #General 日志通道
│   └── errors.py            # 配置/启动异常
└── tests/
    ├── test_bot.py
    ├── test_logging.py
    └── test_config.py
```

边界约定：

- `config.py` 只产生不可变的配置对象，不接触 Telegram。
- `bot.py` 只负责 Telegram 连接、目标校验和主循环，不包含业务动作。
- `logging_setup.py` 只负责日志格式化、缓冲和投递，不参与更新处理或业务动作。
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
5. 如果启用 Telegram 日志通道（默认启用），bot 需要在该群组中拥有发送消息的权限；
   否则日志发送会失败并退回到 stdout，bot 本身仍会继续运行。

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
    max_pending_updates: 100,     // 内存更新队列上限；0=不限制
  },
  runtime: {
    log_level: "INFO",            // stdout 日志等级
    telegram_log_level: "INFO",   // Telegram 日志等级；null / "OFF" 关闭
  },
  programs: {},                   // 预留，当前不执行
}
```

校验规则：

- 未知键会直接报配置错误，防止拼写错误被静默忽略。
- `telegram.group_id` 必须是负整数。
- token 必须符合 `<bot_id>:<secret>` 的基本形式。
- `runtime.log_level` 接受 `DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL` 等 logging 级别名。
- `runtime.telegram_log_level` 接受同样的级别名，默认 `INFO`；`null` 或 `"OFF"` 表示关闭 Telegram 日志通道。
- `polling.max_pending_updates` 必须是非负整数；`0` 表示不限制队列长度。
- `programs` 必须是对象，但内部结构暂不解释。

---

## 5. 启动流程

1. 解析 CLI 参数和 JSON5 配置；失败时退出码为 `2`。此时日志系统尚未配置，错误写到 stderr。
2. 初始化日志：stdout 通道立即启用；Telegram 通道先缓冲，等待目标群组校验完成。
3. 创建 PTB `Application`，不注册任何 handler，更新队列有界，更新严格串行处理。
4. 初始化 bot，调用 `getChat(group_id)`。
5. 校验返回的 chat 必须是 `supergroup`；否则启动失败。若 `is_forum` 为假，记录警告。校验失败时不会向未验证的 chat 发送日志。
6. 绑定 Telegram 日志通道到已验证的群组；之前缓冲的日志会补发到默认 `#General` 话题。
7. 启动应用更新处理器和 `getUpdates` 长轮询。
8. 向 stdout 和 Telegram 输出初始化成功信息，例如：

   ```text
   2026-11-09T12:00:00+0000 INFO     bottg.bot: bottg 0.1.0 initialized | target supergroup 'Ops' (id=-1001234567890 @ops_group) | topics=on | long polling started (timeout=30s)
   ```

9. 等待 `SIGINT` / `SIGTERM`，然后停止 updater、刷新 Telegram 日志、停止 application、关闭 HTTP 客户端。

### 日志通道

**stdout**：同步输出，`runtime.log_level` 控制等级。配置错误仍然直接写到 stderr，因为那时日志系统还没有建立。

**Telegram**：把 `runtime.telegram_log_level` 及以上的日志批量发送到目标群组。发送时省略 `message_thread_id`，在话题群组中即默认 `#General` 话题；非话题群组则发送到主聊天。这个通道是独立基础设施，不经过更新队列或未来的动作队列。

Telegram 通道的行为：

- 最多缓冲 1000 条记录；队列满时丢弃最旧记录，并在下一批消息中报告丢弃数量。
- 每 2 秒聚合一批，消息之间至少间隔 3 秒，以适配群组约 20 条/分钟的限额。
- 单条消息按 UTF-16 计算不超过 4000 个码元（Telegram 上限为 4096）。
- 明确排除 `telegram`、`httpx`、`httpcore` 日志，避免“发送日志产生日志再发送”的反馈循环；这些日志仍然出现在 stdout。
- 两个通道都会把日志文本中的 bot token 替换为 `<redacted>`。
- 发送失败不会影响 bot 运行：本批日志被丢弃，错误直接写到 stdout，并按指数退避重试后续批次。
- 关闭时最多等待 10 秒刷新最后的日志；超时后放弃剩余记录。

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

- 发送业务消息（只有日志通道会向 `#General` 发送日志）
- 注册消息、命令、回调或话题事件 handler
- 调用外部程序/脚本
- 保存任何状态、会话或更新 offset 到磁盘
- Webhook 模式
- 业务动作的限流、重试和幂等性设计（日志通道自身有简单的批量与限流）

`getUpdates` 的 offset 由 PTB 的 `Updater` 在内存中推进，进程重启后不会保留。这是“无状态”定位的有意取舍；未来引入外部程序时，需要明确失败重试和“至少一次/至多一次”的语义。

---

## 7. 动作队列与并发

当前版本**没有动作队列**：没有注册任何 handler，`programs` 只被解析和保留，不会执行。日志输出也不依赖“动作”，它是独立通道，原因：

1. 日志必须在动作系统存在之前就能工作，也要在动作失败后继续工作。
2. 日志投递失败不应触发业务动作，业务动作也不应被日志发送阻塞。
3. 如果日志走动作队列，发送日志本身可能产生新的日志，容易形成递归和死锁。

关于“短时间收到多个会触发钩子的消息”：

- PTB 默认一次只处理一个更新。`build_application` 明确设置 `concurrent_updates=False`（最大并发 1），因此不需要再额外加一把业务锁；按顺序执行的 handler 本身就是串行屏障。
- `polling.max_pending_updates` 给 PTB 的内存更新队列设置了上限。队列满时，轮询会等待，更新留在 Telegram 服务器上，进程内存不会无限增长。
- 只有把整批更新放入队列后，PTB 才推进 `getUpdates` 的 offset。因此进程崩溃或重启时，尚未确认的更新会被 Telegram 重新投递；未来外部动作必须按“至少一次”语义设计为幂等。
- 当前没有业务队列可管理。未来如果动作执行很慢，优先保留“串行处理 + 有界队列 + 背压”，而不是在进程内堆积大量未处理消息。只有确实需要把轮询与动作执行解耦时，才增加独立的有界动作队列，并明确队列满时是阻塞还是丢弃。若届时需要多个 worker，再用 `asyncio.Lock` 或单消费者队列保证关键动作不并行。

日志通道有自己的有界队列和后台任务，但它只承载日志，不参与上述更新处理链路。

---

## 8. 开发检查

```bash
uv run pytest
uv run bottg --version
uv run python -m bottg --help
```

测试不需要真实 token，也不会访问 Telegram。
