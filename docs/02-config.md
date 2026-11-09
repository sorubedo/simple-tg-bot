# 配置

bottg 只读取一个 JSON5 配置文件。JSON5 支持注释、尾逗号和未加引号的键。

默认读取当前目录的 `config.json5`。可以用 `--config` 或 `BOTTG_CONFIG` 指定其他路径：

```bash
uv run bottg --config /etc/bottg/config.json5
BOTTG_CONFIG=/etc/bottg/config.json5 uv run bottg
```

命令行参数优先于环境变量。

完整可运行示例见仓库根目录的 [`config.example.json5`](../config.example.json5)。下面按段说明。

## 完整结构

```json5
{
  telegram: {
    token: "",                    // 可留空，改用 BOTTG_TOKEN
    group_id: -1001234567890,     // 必填，负数
  },

  polling: {
    timeout_seconds: 30,          // 长轮询超时，>= 1
    poll_interval: 0.0,           // 两次轮询的额外间隔，>= 0
    drop_pending_updates: false,  // 启动时丢弃积压更新
    allowed_updates: null,        // null = Telegram 默认更新集合
    max_pending_updates: 100,     // 内存更新队列上限，0 = 不限制
  },

  runtime: {
    log_level: "INFO",            // stdout 日志等级
    telegram_log_level: "INFO",   // Telegram 日志等级，null / "OFF" 关闭
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
      hooks: ["message", "edited_message"],
      timeout_seconds: 10,
      cwd: null,                  // 默认配置文件所在目录
      env: {},                    // 继承环境变量后追加/覆盖
      max_output_bytes: 65536,
      message: {
        topics: null,             // null = 所有话题
        ignore_bots: true,
      },
    },
  },
}
```

## `telegram`

| 字段 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `token` | string | 否 | `""` | bot token。推荐改用 `BOTTG_TOKEN` 环境变量。 |
| `group_id` | integer | 是 | | 目标超级群组 ID，必须是负数，通常形如 `-1001234567890`。 |

token 必须符合 `<bot_id>:<secret>` 的基本形式。`BOTTG_TOKEN` 存在时优先于 `telegram.token`。目标群组在启动时必须能通过 `getChat` 访问，且类型必须是 `supergroup`。

## `polling`

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `timeout_seconds` | integer >= 1 | `30` | `getUpdates` 长轮询等待时间。 |
| `poll_interval` | number >= 0 | `0.0` | 两次轮询之间的额外间隔。 |
| `drop_pending_updates` | boolean | `false` | 启动时是否丢弃 Telegram 服务器上积压的更新。 |
| `allowed_updates` | array / null | `null` | 订阅的更新类型。`null` 表示 Telegram 默认集合。 |
| `max_pending_updates` | integer >= 0 | `100` | 内存中未处理更新的上限。`0` 表示不限制，不建议。 |

`allowed_updates` 的注意点：

- 为 `null` 时，bottg 会向 Telegram 显式发送空数组，即 Telegram 默认的广泛更新集合，包含 `message` 和 `edited_message`。这样不会继承上一次运行或其他工具留下的旧设置。
- 写成非空数组时只订阅列出的类型。如果程序订阅了 `message` 或 `edited_message`，数组里必须包含对应类型，否则启动时会记录警告，对应钩子不会触发。
- 空数组和 `null` 一样，表示 Telegram 默认的广泛更新集合。
- 例：`allowed_updates: ["message", "edited_message"]`。

`max_pending_updates` 限制的是尚未处理的更新数量。队列满时长轮询会等待，未确认的更新留在 Telegram 服务器上，避免内存无限增长。

## `runtime`

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `log_level` | string | `"INFO"` | stdout 日志等级。 |
| `telegram_log_level` | string / null | `"INFO"` | 同步到目标群组 General 话题的日志等级。 |

等级接受 `DEBUG`、`INFO`、`WARNING`、`ERROR`、`CRITICAL` 及其常见别名。

`telegram_log_level` 设为 `null`、`"OFF"` 或 `"NONE"` 时关闭 Telegram 日志通道，只保留 stdout。日志通道的详细行为见[运行行为](05-operations.md#日志)。

## `actions`

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `max_pending` | integer >= 1 | `100` | 最多排队多少个动作组。 |
| `enqueue_timeout_seconds` | number >= 0 | `10` | 队列满时，入队最多等待多少秒。 |
| `drain_timeout_seconds` | number >= 0 | `30` | 关闭时最多等待多少秒排空队列。 |
| `min_interval_seconds` | number >= 0 | `0.0` | 两次 Telegram 动作之间的最小间隔。 |

队列满时，生产者先等待；超过 `enqueue_timeout_seconds` 后整组动作被拒绝并记录错误。`min_interval_seconds` 用于主动降低触发限流的概率，`0` 表示不额外等待。遇到 Telegram 的 `RetryAfter` 时仍会自动等待并重试。

## `programs`

`programs` 是对象，键名就是程序名。每个程序必须有 `command` 和 `hooks`。程序按配置中的顺序执行，JSON5 对象保留键的插入顺序。

| 字段 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `command` | string 数组 | 是 | | 非空。第一项是命令，其余是参数。 |
| `hooks` | string 数组 | 是 | | 非空，取值见下表。重复项会去重。 |
| `timeout_seconds` | number > 0 | 否 | `30` | 单次执行超时。 |
| `cwd` | string / null | 否 | 配置文件目录 | 程序工作目录。相对路径以配置文件目录为基准，目录必须存在。 |
| `env` | object | 否 | `{}` | 在继承 bottg 环境变量后追加或覆盖，值必须是字符串。 |
| `max_output_bytes` | integer >= 1 | 否 | `65536` | stdout 最多保留的字节数，超出视为协议错误。 |
| `message` | object | 否 | 见下 | 仅对 `message` / `edited_message` 钩子有效。 |

`hooks` 可用值：

- `startup`
- `message`
- `edited_message`
- `shutdown`

### `message` 过滤

如果程序的 `hooks` 中包含 `message` 或 `edited_message`，可以加 `message` 段：

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `topics` | array / null | `null` | `null` 表示所有话题。数组中的正整数表示指定话题，`null` 表示 General 话题。 |
| `ignore_bots` | boolean | `true` | 是否忽略其他 bot 用户发送的消息。 |

如果 `hooks` 中没有消息类钩子，配置里出现 `message` 段会报配置错误。

`ignore_bots` 只过滤 `from_user.is_bot` 为真的消息。匿名管理员或频道身份的消息不会因为这个选项被过滤。关闭后两个 bot 可能互相触发，需要自行防止循环。

### 命令路径

- `command` 不使用 shell，每一项都作为独立 argv 传递。
- 需要 shell 时显式写：`["/bin/sh", "-c", "..."]`。
- `command[0]` 是裸命令名时按 `PATH` 查找，例如 `"python3"`。
- 相对路径按程序的 `cwd` 解析。
- `cwd` 默认为配置文件所在目录，因此示例中的 `examples/echo_hook.py` 默认能直接找到。

### 配置示例

一个最小的消息钩子程序：

```json5
{
  telegram: {
    group_id: -1001234567890,
  },
  programs: {
    echo: {
      command: ["python3", "examples/echo_hook.py"],
      hooks: ["message"],
    },
  },
}
```

一个只处理指定话题、并关闭 bot 过滤的程序：

```json5
{
  programs: {
    watcher: {
      command: ["/opt/hooks/watcher"],
      hooks: ["message", "edited_message"],
      cwd: "/opt/hooks",
      env: { MODE: "production" },
      timeout_seconds: 5,
      message: {
        topics: [17, 42, null],
        ignore_bots: false,
      },
    },
  },
}
```

## 校验规则

配置错误会导致退出码 `2`，错误信息写到 stderr。

- 所有层级都拒绝未知键，拼写错误不会被静默忽略。
- 顶层只允许 `telegram`、`polling`、`runtime`、`actions`、`programs`。
- `telegram` 必须存在，`telegram.group_id` 必须是负整数。
- token 必须存在且格式合法。
- `polling.timeout_seconds` 必须是 >= 1 的整数。
- `polling.poll_interval`、`actions.enqueue_timeout_seconds`、`actions.drain_timeout_seconds`、`actions.min_interval_seconds` 必须是非负数。
- `polling.max_pending_updates` 必须是非负整数。
- `actions.max_pending` 必须是 >= 1 的整数。
- `programs` 必须是对象，每个程序必须是对象。
- `command` 必须是非空字符串数组，每一项都非空。
- `hooks` 必须是非空数组，且只能包含四种钩子名。
- `timeout_seconds` 必须大于 0。
- `cwd` 必须是已存在的目录。
- `env` 的键和值都必须是字符串。
- `max_output_bytes` 必须是正整数。
- `message.topics` 必须是 `null` 或非空数组，数组项是正整数或 `null`。

## 相关文档

- 程序收到什么：[钩子](03-hooks.md)
- 程序能返回什么：[动作](04-actions.md)
- 队列、日志和关闭行为：[运行行为](05-operations.md)
