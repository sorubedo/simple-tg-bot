# bottg

一个只管理单个 Telegram 超级群组（话题群组）的机器人骨架。

bottg 只负责 Telegram 连接、更新接收、生命周期和动作执行。业务逻辑放在配置指定的外部程序里：程序从 stdin 读取钩子事件 JSON，从 stdout 返回动作 JSON。这样业务代码可以用任意语言编写，主程序也不需要知道业务状态。

当前版本（0.2.0）已经包含：

- JSON5 配置加载、Telegram 连接、目标群组校验和长轮询。
- `startup`、`message`、`shutdown` 三类钩子。
- 串行、有界、可关闭排空的动作队列。
- 四个动作：`send_message`、`delete_message`、`set_reaction`、`sleep`。
- stdout 日志和 Telegram `#General` 日志双通道。
- 外部程序的 stdin/stdout JSON 协议和严格校验。

设计约束：

- **无状态**：不使用数据库、会话文件或 PTB persistence。业务状态不留在 bottg 进程内。
- **单群组边界**：动作协议中没有 `chat_id` 或 `group_id` 字段。所有动作只会作用在配置的超级群组内，外挂程序不需要也不应该关心群组之外的对象。
- **配置驱动**：唯一配置文件使用 JSON5，支持注释、尾逗号和未加引号的键。
- **顺序执行**：消息更新由 PTB 串行处理，钩子程序按配置顺序执行，动作队列只有一个 worker。慢任务会形成背压，而不是产生并发副作用。
- **失败不扩散**：单个外挂程序失败、超时或返回非法输出时，只记录日志，不影响其他程序和 bottg 主循环。

---

## 1. 技术选型

### `python-telegram-bot==22.8`

选择它作为 Telegram Bot API 客户端，原因：

1. **与项目的 Python 3.15.2 兼容**。截至搭建时，`aiogram 3.31.0` 的 `requires-python` 是 `>=3.10,<3.15`，无法安装到本项目的 3.15.2 环境；`python-telegram-bot 22.8` 要求 `>=3.10`，没有 3.15 上限。
2. 成熟、维护活跃，原生 asyncio，覆盖 `getUpdates` 长轮询、更新队列、生命周期和优雅关闭。
3. 直接提供 handler、filter、chat/topic 过滤能力，不需要自己搭一套更新分发基础设施。
4. 使用 bot token，不需要 MTProto 的 `api_id` / `api_hash` 和会话文件。

### `json5==0.17.3`

只负责解析 JSON5。配置文件里可以写注释和尾逗号，便于说明钩子和动作配置。

### 外部程序执行

使用 `asyncio.create_subprocess_exec` 直接执行 argv 数组，不经过 shell。stdin/stdout/stderr 都是管道，读取时有超时和字节上限。没有新增运行时依赖。

### 开发依赖

- `pytest`：配置、动作协议、动作队列、程序协议和生命周期的单元测试。

### 为什么不用其他方案

- **aiogram**：当前版本的 Python 上限与项目固定的 3.15.2 冲突。如果以后降级 Python 到 3.14 或以下，可以重新评估。
- **Telethon / Pyrogram**：它们是 MTProto 客户端，通常以用户身份登录并依赖 `.session` 文件，与“bot token + 无状态”的定位不符。
- **裸 `httpx`**：长轮询的 offset、错误重试、生命周期和信号处理都要自己实现，现阶段不值得。

---

## 2. 目录结构

```text
.
├── config.example.json5     # 可提交的配置示例
├── config.json5             # 本地实际配置（已被 .gitignore 忽略）
├── examples/
│   ├── echo_hook.py         # 示例 message 钩子：回复 !echo ...
│   └── lifecycle_hook.py    # 示例 startup/shutdown 钩子
├── pyproject.toml
├── src/bottg/
│   ├── __init__.py          # 版本号
│   ├── __main__.py          # python -m bottg
│   ├── cli.py               # 参数、日志、退出码
│   ├── config.py            # JSON5 加载与强校验
│   ├── bot.py               # PTB 生命周期、群组校验、钩子编排
│   ├── actions.py           # 动作模型与 stdout 动作 JSON 解析
│   ├── action_queue.py      # 串行有界动作队列与 Telegram 执行器
│   ├── programs.py          # 外部程序 stdin/stdout 协议与调度
│   ├── logging_setup.py     # stdout 与 Telegram #General 日志通道
│   └── errors.py            # 配置/启动/动作/程序异常
└── tests/
    ├── test_actions.py
    ├── test_action_queue.py
    ├── test_bot.py
    ├── test_config.py
    ├── test_logging.py
    └── test_programs.py
```

边界约定：

- `config.py` 只产生不可变的配置对象，不接触 Telegram，也不执行程序。
- `bot.py` 只负责 Telegram 连接、目标校验、生命周期和把钩子/队列接起来。
- `actions.py` 只负责动作模型和 stdout JSON 解析，不执行动作。
- `action_queue.py` 只负责排队、限流、重试和调用 Telegram API，不启动外部程序。
- `programs.py` 只负责执行外部程序、构造 stdin、解析退出状态和 stdout，不直接调用 Telegram API。
- `logging_setup.py` 是独立日志通道，不经过更新队列或动作队列。

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
4. 如果要执行删除消息、贴表情等动作，bot 需要相应的群组权限。删除别人的消息通常需要管理员权限；贴表情需要群组允许该反应。
5. 如果 bot 开启了 BotFather 的 privacy mode，并且不是群组管理员，它只会收到命令、提及和回复，普通消息不会到达 `message` 钩子。可以把 bot 设为管理员，或在 BotFather 中关闭 privacy mode，然后重启 bottg。
6. 长轮询和 webhook 互斥。PTB 在开始轮询的 bootstrap 阶段会自动调用 `deleteWebhook`，因此 bottg 会接管该 bot 的更新获取方式。如果这个 bot 同时被其他 webhook 服务使用，请为 bottg 使用独立的 bot token。
7. 如果启用 Telegram 日志通道（默认启用），bot 需要在该群组中拥有发送消息的权限；否则日志发送会失败并退回 stdout，bot 本身仍会继续运行。

### 示例钩子

`config.example.json5` 包含两个示例程序：

- `examples/echo_hook.py`：在群里回复以 `!echo ` 或 `/echo ` 开头的消息。
- `examples/lifecycle_hook.py`：在启动和关闭时向 General 话题发送一条状态消息。

在群里发送：

```bash
!echo 你好
# 或，在 privacy mode 下更可靠
/echo 你好
```

机器人会向同一个话题发送一条回复。其他消息不产生 stdout，因此不执行任何动作。

---

## 4. 配置说明

完整示例见 `config.example.json5`。下面是结构摘要：

0.1.0 中的 `programs` 只是预留字段，不会被解析成可执行配置。0.2.0 起每个程序必须提供 `command` 和 `hooks`，旧配置如果只写了 `command` 会得到配置错误。

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
    allowed_updates: null,        // null=Telegram 默认集合（含 message）
    max_pending_updates: 100,     // 内存更新队列上限；0=不限制
  },

  runtime: {
    log_level: "INFO",            // stdout 日志等级
    telegram_log_level: "INFO",   // Telegram 日志等级；null / "OFF" 关闭
  },

  actions: {
    max_pending: 100,             // 最多排队多少个动作组
    enqueue_timeout_seconds: 10,  // 入队最多等待秒数
    drain_timeout_seconds: 30,    // 关闭时最多排空秒数
    min_interval_seconds: 0.0,    // 两次 Telegram 动作之间的最小间隔
  },

  programs: {
    echo: {
      command: ["python3", "examples/echo_hook.py"],
      hooks: ["message"],         // startup / message / shutdown
      timeout_seconds: 10,
      cwd: null,                  // 默认配置文件所在目录
      env: {},                    // 继承环境变量后追加/覆盖
      max_output_bytes: 65536,
      message: {
        topics: null,             // null=所有话题
        ignore_bots: true,
      },
    },
  },
}
```

### 校验规则

- 未知键会直接报配置错误，防止拼写错误被静默忽略。
- `telegram.group_id` 必须是负整数。
- token 必须符合 `<bot_id>:<secret>` 的基本形式。
- `runtime.log_level` 接受 `DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL` 等级别名。
- `runtime.telegram_log_level` 接受同样的等级名，默认 `INFO`；`null` 或 `"OFF"` 表示关闭 Telegram 日志通道。
- `polling.max_pending_updates` 必须是非负整数；`0` 表示不限制队列长度。
- `polling.allowed_updates` 为 null 时，bottg 会显式向 Telegram 发送空数组，即 Telegram 默认的广泛更新集合（包含 `message`）。这样不会继承上一次运行或其他工具留下的旧 `allowed_updates` 设置。如果要限制更新类型，显式写出数组。
- `actions.max_pending` 必须是正整数；队列满时生产者先等待，等待超时后整组拒绝并记录错误。
- `actions.enqueue_timeout_seconds`、`drain_timeout_seconds`、`min_interval_seconds` 必须是非负数。
- `programs` 必须是对象。每个程序必须有非空的 `command` 数组和 `hooks` 数组。
- `command` 不使用 shell。需要 shell 时显式写 `["/bin/sh", "-c", "..."]`。
- `command[0]` 如果是相对路径，按程序的工作目录（`cwd`）解析；裸命令名按 `PATH` 查找。
- `hooks` 只能包含 `startup`、`message`、`shutdown`，至少一个，重复项会去重。
- `timeout_seconds` 必须大于 0，默认 30。
- `cwd` 为 null 时使用配置文件所在目录。相对 `cwd` 也以配置文件所在目录为基准。目录必须存在。
- `env` 的值必须是字符串。程序继承 bottg 进程的环境变量，然后应用 `env` 中的覆盖项。
- `max_output_bytes` 必须是正整数，默认 65536。stdout 超过上限会被视为协议错误。
- `message` 段只对 `message` 钩子有效。如果 `hooks` 中没有 `message`，配置中出现 `message` 段会报错。
- `message.topics` 为 null 表示所有话题；数组中的正整数表示指定话题；数组中的 null 表示 General（没有 `message_thread_id`）话题。
- `message.ignore_bots` 默认 true，忽略其他 bot 发送的消息。关闭后两个 bot 可能互相触发，需要自行防止循环。
- 如果某个程序订阅了 `message` 钩子，但 `polling.allowed_updates` 不是 null 且不包含 `"message"`，启动时会记录警告，消息钩子不会触发。

程序按配置中的顺序执行。JSON5 对象保留键的插入顺序，因此可以用这个顺序控制多个程序的执行顺序。

---

## 5. 生命周期与钩子

### 启动顺序

1. 解析 CLI 参数和 JSON5 配置；失败时退出码为 `2`。此时日志系统尚未配置，错误写到 stderr。
2. 初始化日志：stdout 通道立即启用；Telegram 通道先缓冲。
3. 创建 PTB `Application`，更新队列有界，更新严格串行处理。
4. 初始化 bot，调用 `getChat(group_id)`。
5. 校验返回的 chat 必须是 `supergroup`；否则启动失败。若 `is_forum` 为假，记录警告。
6. 绑定 Telegram 日志通道到已验证的群组；之前缓冲的日志补发到默认 `#General` 话题。
7. `app.start()`，然后创建并启动动作队列。
8. 创建程序调度器，注册消息 handler。
9. 按配置顺序执行 `startup` 钩子。外挂程序可以把动作放入动作队列，队列 worker 会同时开始执行。
10. 启动 `getUpdates` 长轮询。
11. 输出就绪日志，例如：

    ```text
    2026-11-09T12:00:00+0000 INFO     bottg.bot: bottg 0.2.0 initialized | target supergroup 'Ops' (id=-1001234567890 @ops_group) | topics=on | programs=1 | long polling started (timeout=30s)
    ```

### 关闭顺序

收到 `SIGINT` 或 `SIGTERM` 后：

1. 停止 updater 的轮询。
2. 调用 `Application.stop()`，等待已经进入更新队列的消息处理完。此后不会再触发新的 `message` 钩子。
   如果队列里积压了很多消息，而消息钩子又很慢，关闭会相应变慢。可以通过调小
   `polling.max_pending_updates` 和程序 `timeout_seconds` 控制最坏情况。
3. 按配置顺序执行 `shutdown` 钩子。此时 bot 还没有关闭，Telegram API 仍然可用，因此关闭钩子可以发送消息、删除消息或贴表情。
4. 排空动作队列，最多等待 `actions.drain_timeout_seconds`。超时会取消当前动作并丢弃剩余动作。
5. 刷新并关闭 Telegram 日志通道。
6. 退出 `Application` 上下文，关闭 bot HTTP 客户端。

`shutdown` 钩子的 stdin 中，`reason` 为：

- `"signal"`：由 `SIGINT` / `SIGTERM` 正常触发。
- `"error"`：启动后因异常或外部取消进入关闭流程。

### 钩子语义

| 钩子 | 触发时机 | 是否有 `message` |
| --- | --- | --- |
| `startup` | 目标群组校验完成、动作队列启动后，长轮询开始前 | 否 |
| `message` | 目标群组收到新的消息更新 | 是 |
| `shutdown` | 轮询和更新处理停止后，动作队列关闭前 | 否 |

- `message` 钩子只对配置的超级群组触发。其他群组、频道、私聊的更新不会进入外挂程序。
- `message` 钩子只处理新消息。编辑消息、频道帖子、回调查询等更新不会触发，除非以后增加新的钩子。
- `message` 可能是服务消息。`text` 和 `caption` 可能为 null，外挂程序应自行判断。
- 所有钩子按配置顺序执行。一个程序超时、崩溃或返回非法输出时，记录日志并继续执行下一个程序。
- 消息钩子运行在 PTB 的单更新处理链上。慢程序会延迟后续消息处理，这是有意的背压设计。更新队列由 `polling.max_pending_updates` 限制。
- 启动钩子失败不会阻止 bottg 启动。关闭钩子失败不会阻止 bottg 关闭。

---

## 6. 外挂程序 stdin 协议

每次执行一个程序时，bottg 会向它的 stdin 写入一个 UTF-8 JSON 对象，然后关闭 stdin。对象以换行结尾。

### `message` 钩子示例

```json
{
  "protocol": "bottg.hook/1",
  "hook": "message",
  "program": "echo",
  "bottg": { "version": "0.2.0" },
  "group": {
    "id": -1001234567890,
    "title": "Ops",
    "username": "ops_group",
    "is_forum": true
  },
  "topic": { "id": 17 },
  "message": {
    "id": 123,
    "date": "2026-11-09T12:00:00+00:00",
    "text": "!echo 你好",
    "caption": null,
    "sender": {
      "type": "user",
      "id": 42,
      "is_bot": false,
      "username": "alice",
      "first_name": "Alice",
      "last_name": null
    },
    "reply_to_message_id": null,
    "message_thread_id": 17,
    "is_topic_message": true
  },
  "update_id": 987654321,
  "reason": null
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `protocol` | 固定为 `bottg.hook/1`。后续不兼容变更会提升版本。 |
| `hook` | `startup`、`message` 或 `shutdown`。 |
| `program` | 配置中的程序名。 |
| `bottg.version` | bottg 版本。 |
| `group` | 已验证的目标群组。`id` 是该群组的数字 ID。 |
| `group.is_forum` | 是否开启 Topics。 |
| `topic.id` | 消息所属话题的 `message_thread_id`。null 表示 General 或非话题群组。 |
| `message` | `message` 钩子的事件对象；其他钩子为 null。 |
| `message.id` | 消息 ID，可用于删除或贴表情。 |
| `message.date` | ISO 8601 时间。 |
| `message.text` | 文本内容；非文本消息可能为 null。 |
| `message.caption` | 媒体标题；没有标题时为 null。 |
| `message.sender` | 有效发送者。匿名管理员或频道身份会是 `type: "chat"`。 |
| `message.reply_to_message_id` | 被回复消息的 ID；不是回复时为 null。 |
| `message.message_thread_id` | 与 `topic.id` 相同。 |
| `message.is_topic_message` | Bot API 的 `is_topic_message`。 |
| `update_id` | Telegram 更新 ID；启动和关闭钩子为 null。 |
| `reason` | 关闭原因；其他钩子为 null。 |

### `startup` / `shutdown` 示例

```json
{
  "protocol": "bottg.hook/1",
  "hook": "startup",
  "program": "lifecycle",
  "bottg": { "version": "0.2.0" },
  "group": {
    "id": -1001234567890,
    "title": "Ops",
    "username": "ops_group",
    "is_forum": true
  },
  "topic": { "id": null },
  "message": null,
  "update_id": null,
  "reason": null
}
```

`shutdown` 钩子的 `reason` 会是 `"signal"` 或 `"error"`。

外挂程序的诊断输出应写到 stderr。stdout 只用于动作 JSON。

---

## 7. stdout 动作协议

### 基本规则

- stdout 可以为空。**退出码为 0 且 stdout 为空表示成功且没有动作。** 外挂程序不需要输出 `noop` 或 `actions: []`。
- stdout 必须是一个 UTF-8 JSON 文档，不是 JSON Lines，也不接受 JSON5 注释或尾逗号。诊断信息写 stderr，不要混入 stdout。
- 退出码非 0、超时、stdout 超过 `max_output_bytes`、stdout 不是合法 UTF-8、stdout 不是合法动作 JSON，都会被视为本次调用失败。此时 stdout 中的动作全部丢弃。
- 动作 JSON 是“全有或全无”：只要有一个动作字段非法，整个 stdout 的动作组都不会入队。
- 动作按数组顺序执行。动作组作为一个整体入队，不会出现只入队一半的情况。
- 单个动作执行失败不会阻止同一组中后续动作。失败会记录日志，队列继续。
- 动作是异步执行的。外挂程序退出后不会收到动作执行结果，需要结果时查看日志或使用外部状态。
- 动作协议中没有 `chat_id` 或 `group_id`。所有动作只能作用于目标超级群组。

### 支持的三种形式

单个动作：

```json
{ "action": "send_message", "text": "hello" }
```

动作数组：

```json
[
  { "action": "set_reaction", "emoji": "👍" },
  { "action": "sleep", "seconds": 1.5 },
  { "action": "send_message", "text": "done" }
]
```

带协议头的动作组：

```json
{
  "protocol": "bottg.actions/1",
  "actions": [
    { "action": "delete_message", "message_id": 123 },
    { "action": "send_message", "text": "已删除" }
  ]
}
```

`protocol` 是可选的；如果提供，目前只接受 `bottg.actions/1`。

### 上下文默认值

为了让外挂程序不需要关心群组和话题，动作会从触发钩子继承上下文：

- 在 `message` 钩子中，省略 `topic_id` 时使用触发消息所在话题；省略 `message_id` 时使用触发消息 ID。
- 在 `startup` / `shutdown` 钩子中，`topic_id` 默认为 null（General 话题），`message_id` 没有默认值。
- `send_message` 的 `topic_id` 显式写 null 表示强制发到 General，即使触发消息在某个话题中。
- `delete_message` 和 `set_reaction` 在 `startup` / `shutdown` 钩子中必须显式提供 `message_id`。

示例：在触发消息所在话题回复，并引用触发消息：

```json
{
  "action": "send_message",
  "text": "收到",
  "reply_to_message_id": 123
}
```

`reply_to_message_id` 省略或为 null 表示不引用。

---

## 8. 动作列表

### `send_message`

发送一条文本消息。

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `action` | string | 是 | | 固定为 `send_message` |
| `text` | string | 是 | | 非空，最多 4096 个 UTF-16 码元 |
| `topic_id` | integer / null | 否 | 触发话题 | 正整数；null 表示 General |
| `reply_to_message_id` | integer / null | 否 | null | 引用的消息 ID |
| `parse_mode` | string / null | 否 | null | `HTML`、`Markdown` 或 `MarkdownV2`，大小写不敏感 |
| `disable_notification` | boolean | 否 | false | 静默发送 |
| `disable_web_page_preview` | boolean | 否 | false | 关闭链接预览 |

### `delete_message`

删除目标群组中的一条消息。

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `action` | string | 是 | | 固定为 `delete_message` |
| `message_id` | integer | 否 | 触发消息 | 正整数；没有触发消息时必须提供 |

### `set_reaction`

给一条消息贴一个标准 emoji 表情。bot 每次只设置一个反应。

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `action` | string | 是 | | 固定为 `set_reaction` |
| `message_id` | integer | 否 | 触发消息 | 正整数；没有触发消息时必须提供 |
| `emoji` | string | 是 | | 非空标准 emoji；不支持自定义 emoji |
| `is_big` | boolean | 否 | false | 是否使用大号动画 |

### `sleep`

暂停动作队列。它只延迟后续动作，不延迟同一钩子中下一个外挂程序的启动，也不阻塞 Telegram 更新接收本身。

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `action` | string | 是 | | 固定为 `sleep` |
| `seconds` | number | 是 | | 大于 0，最大 3600 |

### 动作错误

以下情况会被拒绝：

- 未知的 `action`。
- 动作对象中出现未知字段，例如 `chat_id`。
- 字段类型错误，例如把 `message_id` 写成字符串。
- `topic_id` 或 `message_id` 不是正整数。
- `text` 为空或超过长度限制。
- `parse_mode` 不在允许列表中。
- `sleep.seconds` 小于等于 0、超过 3600 或不是有限数字。
- 在启动/关闭钩子中省略了 `delete_message.message_id` 或 `set_reaction.message_id`。

---

## 9. 动作队列语义

### 顺序与并发

- 只有一个 worker，按 FIFO 执行动作组。
- 消息更新由 PTB 串行处理；一个慢钩子程序会延迟后续消息，但不会让动作并发执行。
- 启动钩子的动作会排在长轮询开始前入队；消息动作排在它们之后；关闭钩子的动作排在已入队的消息动作之后。
- 关闭时先停止更新处理，再运行关闭钩子，因此关闭钩子的动作不会和消息钩子竞争。

### 有界队列与背压

- `actions.max_pending` 限制等待中的动作组数量，而不是单个动作数量。
- 队列满时，生产者等待最多 `actions.enqueue_timeout_seconds`。如果仍然没有空间，整个动作组被拒绝并记录错误。
- 动作组不会部分入队，因此不会出现“前半组执行了、后半组丢了”的情况。

### 限流和重试

- `actions.min_interval_seconds` 控制两次 Telegram 动作之间的最小间隔。
- 如果 Telegram 返回 `RetryAfter`，队列会等待指定时间后重试，最多尝试 3 次。
- 单次 `RetryAfter` 等待超过 120 秒时不再重试。
- 其他 Telegram 错误，例如消息不存在、没有删除权限、反应不可用，会记录警告并跳过该动作。
- 非 Telegram 异常会记录完整堆栈并跳过该动作。

### 关闭排空

- 关闭钩子执行完后，动作队列会等待已入队动作完成，最多 `actions.drain_timeout_seconds`。
- 超时会取消当前正在执行的动作，并丢弃剩余动作。
- `sleep` 动作可以被关闭取消。

### 语义边界

- `getUpdates` 的 offset 只保存在内存中。进程崩溃或重启后，尚未确认的更新可能被 Telegram 重新投递，外挂程序可能再次运行。
- 动作不是幂等的。需要去重时，外挂程序可以使用 `update_id`、`message.id` 或自己的外部状态。
- 动作队列不持久化。进程退出时未执行的动作会丢失。

---

## 10. 日志与错误处理

**stdout**：同步输出，`runtime.log_level` 控制等级。配置错误仍然直接写到 stderr，因为那时日志系统还没有建立。

**Telegram**：把 `runtime.telegram_log_level` 及以上的日志批量发送到目标群组。发送时省略 `message_thread_id`，在话题群组中即默认 `#General` 话题；非话题群组则发送到主聊天。这个通道是独立基础设施，不经过更新队列或动作队列。

Telegram 通道的行为：

- 最多缓冲 1000 条记录；队列满时丢弃最旧记录，并在下一批消息中报告丢弃数量。
- 每 2 秒聚合一批，消息之间至少间隔 3 秒，以适配群组约 20 条/分钟的限额。
- 单条消息按 UTF-16 计算不超过 4000 个码元（Telegram 上限为 4096）。
- 排除 `httpx`、`httpcore` 以及 PTB 的 INFO/DEBUG 日志，避免“发送日志产生日志再发送”的反馈循环；`telegram.ext` 的 WARNING/ERROR（例如轮询网络错误）会同步到群组，所有被排除的日志仍然出现在 stdout。
- 两个通道都会把日志文本中的 bot token 替换为 `<redacted>`。
- 发送失败不会影响 bot 运行：本批日志被丢弃，错误直接写到 stdout，并按指数退避重试后续批次。
- 关闭时最多等待 10 秒刷新最后的日志；超时后放弃剩余记录。

外挂程序的错误处理：

- 程序启动失败、超时、stdout 超限、stdout 非 UTF-8：记录警告，跳过该程序。
- 退出码非 0：记录警告，丢弃 stdout 动作。
- stderr：程序失败时用 WARNING 记录；成功时用 DEBUG 记录。最多保留 16 KiB，超出部分丢弃并追加 `[truncated]` 标记。
- stdout JSON 非法或动作字段非法：记录错误，丢弃整个动作组。
- 动作队列已满或已关闭：记录错误，丢弃该动作组。
- 单个程序失败不会阻止同一钩子的其他程序。

退出码：

| 退出码 | 含义 |
| --- | --- |
| `0` | 正常停止，包括收到 `SIGINT` / `SIGTERM` 后优雅退出 |
| `1` | 启动失败、Telegram API 错误或未预期异常 |
| `2` | 配置文件缺失、JSON5 语法错误或校验失败 |
| `130` | 信号处理器安装前或平台限制下收到 `KeyboardInterrupt` |

---

## 11. 当前版本明确不做的事

- 发送图片、文件、投票等非文本消息。
- 编辑、转发、置顶、复制消息。
- 自定义 emoji 反应或一次设置多个反应。
- 处理编辑消息、频道帖子、回调查询等非 `message` 更新。
- 并行执行多个外挂程序，或并行执行一个程序的多份实例。
- 持久化动作队列、更新 offset 或任何业务状态。
- 对非 `RetryAfter` 的 Telegram 错误自动重试。
- 自动重试失败的外挂程序。消息钩子只有在收到下一条消息时才会再次运行。
- Shell 展开、环境变量展开或 `command` 字符串形式。
- 让动作访问目标群组之外的聊天。
- Webhook 模式。

---

## 12. 开发检查

```bash
uv run pytest
uv run bottg --version
uv run python -m bottg --help
```

测试不需要真实 token，也不会访问 Telegram。程序协议测试使用本地 Python 子进程验证 stdin、stdout、stderr、超时和环境变量行为。

手动测试示例钩子：

```bash
printf '%s\n' '{"message":{"id":5,"text":"!echo hello"}}' \
  | uv run python examples/echo_hook.py
```

输出应为：

```json
{"action": "send_message", "text": "hello", "reply_to_message_id": 5}
```

生命周期示例：

```bash
printf '%s\n' '{"hook":"shutdown","bottg":{"version":"0.2.0"},"reason":"signal"}' \
  | uv run python examples/lifecycle_hook.py
```

输出应为：

```json
{"action": "send_message", "text": "bottg stopping (signal)"}
```

---

## 13. 常见问题：`message` 钩子不触发

按顺序检查：

1. **privacy mode / 管理员状态**。BotFather 中 privacy mode 开启时，非管理员的 bot 只收到命令、提及、回复和它自己发送的消息。bottg 启动时会查询 bot 的 privacy mode 和管理员状态；如果两者都不满足，会记录警告。把 bot 设为群组管理员，或在 BotFather 关闭 privacy mode，然后重启 bottg。
2. **`allowed_updates`**。bottg 现在总会显式发送 `allowed_updates`。如果配置里显式写了数组但不包含 `"message"`，消息钩子不会触发，启动时会记录警告。删除该项或写成 `null` 可恢复 Telegram 默认集合。
3. **发送者**。`message.ignore_bots` 默认 true，其他 bot 发送的消息会被忽略。如果用另一个 bot 测试，把它改成 false。
4. **目标群组**。消息必须发在 `telegram.group_id` 对应的超级群组里。私聊、其他群组和其他频道不会触发。
5. **消息类型**。只处理目标超级群组里的新消息。编辑消息、频道帖子、回调查询不会触发；服务消息可能触发，但 `text`/`caption` 可能为 null。
6. **命令格式**。示例脚本只响应以 `!echo ` 或 `/echo ` 开头的消息，注意 `echo` 后面有一个空格，例如 `!echo 你好`、`/echo 你好`。在群里也可以写 `/echo@你的bot用户名 你好`。
7. **程序路径**。`command` 的相对路径按 `cwd` 解析，`cwd` 默认是配置文件所在目录。启动日志和程序 stderr 会报告启动失败、超时或非法 stdout。
8. **提高日志等级**。把 `runtime.log_level` 改成 `"DEBUG"`，可以看到 `message hook trigger` 日志，确认 handler 是否收到消息，以及程序是否被过滤。
9. **验证动作链路**。示例 `lifecycle` 程序会在启动时向 General 发送一条消息。如果连它也没有出现，优先检查 bot 的发送权限、privacy mode 和日志通道。
