# 钩子

钩子是 bottg 调用外部程序的时机。每次触发都会启动一个新的子进程，把事件 JSON 写入 stdin，然后关闭 stdin。

## 四种钩子

| 钩子 | 触发时机 | 有 `message` |
| --- | --- | --- |
| `startup` | 目标群组校验完成、动作队列启动后，长轮询开始前 | 否 |
| `message` | 目标群组收到新消息 | 是 |
| `edited_message` | 目标群组中的消息被编辑 | 是，内容为编辑后的版本 |
| `shutdown` | 轮询和更新处理停止后，动作队列关闭前 | 否 |

`message` 和 `edited_message` 只对 `telegram.group_id` 指定的超级群组触发。其他群组、频道、私聊的更新不会进入程序。

频道帖子、回调查询等更新不会触发。`message` 只处理新消息，编辑消息需要单独订阅 `edited_message`。

## 执行方式

- 每次事件启动一个新进程。程序不需要常驻，也不要在两次调用之间依赖进程内状态。
- 程序继承 bottg 的环境变量，然后应用 `env` 中的覆盖项。
- 程序按配置中的顺序执行。前一个程序结束后才启动下一个。
- 消息钩子运行在 Telegram 更新的串行处理链上。慢程序会延迟后续消息，这是有意的背压。
- 一个程序失败、超时或返回非法输出，只记录日志，继续执行下一个程序。
- 启动钩子失败不会阻止启动，关闭钩子失败不会阻止关闭。
- 程序执行完就退出，bottg 不会把动作的执行结果回传给程序。

## 过滤规则

对 `message` 和 `edited_message`，程序只会在满足以下条件时运行：

1. 消息属于目标超级群组。
2. 如果 `message.ignore_bots` 为 `true`（默认），发送者不是 bot 用户。
3. 如果配置了 `message.topics`，消息所在话题在列表中。`null` 表示 General 话题或非话题群组。

`message` 和 `edited_message` 共用同一个 `message` 过滤配置。

## stdin 格式

bottg 向 stdin 写入一个 UTF-8 JSON 对象，以换行结尾，然后关闭 stdin。程序可以从 stdin 读到 EOF。

程序应读取完整 JSON 后再处理。诊断信息写到 stderr，stdout 只用于动作 JSON。

### `message` 示例

```json
{
  "protocol": "bottg.hook/1",
  "hook": "message",
  "program": "echo",
  "bottg": { "version": "0.3.0" },
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
    "edit_date": null,
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

### `edited_message` 示例

```json
{
  "protocol": "bottg.hook/1",
  "hook": "edited_message",
  "program": "echo",
  "bottg": { "version": "0.3.0" },
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
    "edit_date": "2026-11-09T12:05:00+00:00",
    "text": "!echo 编辑后的内容",
    "caption": null,
    "sender": { "type": "user", "id": 42, "is_bot": false },
    "reply_to_message_id": null,
    "message_thread_id": 17,
    "is_topic_message": true
  },
  "update_id": 987654322,
  "reason": null
}
```

`message.text` / `message.caption` 是编辑后的内容。bottg 不保存编辑前的版本，需要历史时由程序自己保存。

### `startup` / `shutdown` 示例

```json
{
  "protocol": "bottg.hook/1",
  "hook": "startup",
  "program": "lifecycle",
  "bottg": { "version": "0.3.0" },
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

`shutdown` 的 `reason`：

- `"signal"`：收到 `SIGINT` / `SIGTERM`，正常关闭。
- `"error"`：启动后因异常或外部取消进入关闭流程。

其他钩子的 `reason` 为 `null`。

## 字段说明

| 字段 | 说明 |
| --- | --- |
| `protocol` | 固定为 `bottg.hook/1`。不兼容变更会提升版本。 |
| `hook` | `startup`、`message`、`edited_message` 或 `shutdown`。 |
| `program` | 配置中的程序名。 |
| `bottg.version` | bottg 版本。 |
| `group` | 已验证的目标群组。 |
| `group.id` | 群组数字 ID。 |
| `group.title` | 群组标题。 |
| `group.username` | 群组公开用户名，没有则为 `null`。 |
| `group.is_forum` | 是否开启 Topics。 |
| `topic.id` | 消息所属话题的 `message_thread_id`。`null` 表示 General 或非话题群组。 |
| `message` | 消息钩子的事件对象，其他钩子为 `null`。 |
| `message.id` | 消息 ID，可用于删除或贴表情。 |
| `message.date` | 原始发送时间，ISO 8601。 |
| `message.edit_date` | 编辑时间，ISO 8601。未编辑为 `null`，`edited_message` 中一定有值。 |
| `message.text` | 文本内容，非文本消息可能为 `null`。 |
| `message.caption` | 媒体标题，没有则为 `null`。 |
| `message.sender` | 有效发送者，可能是用户、匿名管理员或频道身份。 |
| `message.sender.type` | `"user"` 或 `"chat"`。`"chat"` 表示匿名管理员或频道身份。 |
| `message.sender.is_bot` | 仅 `type` 为 `"user"` 时存在。 |
| `message.reply_to_message_id` | 被回复消息的 ID，不是回复则为 `null`。 |
| `message.message_thread_id` | 与 `topic.id` 相同。 |
| `message.is_topic_message` | Telegram Bot API 的 `is_topic_message`。 |
| `update_id` | Telegram 更新 ID。启动和关闭钩子为 `null`。 |
| `reason` | 关闭原因，其他钩子为 `null`。 |

`message` 可能是服务消息，例如成员加入、标题变更。这类消息的 `text` 和 `caption` 都可能为 `null`，程序应自行判断。

## 错误与输出限制

| 情况 | 行为 |
| --- | --- |
| 程序启动失败 | 记录警告，跳过该程序 |
| 超时 | 杀死进程，记录警告，跳过该程序 |
| stdout 超过 `max_output_bytes` | 视为失败，丢弃输出 |
| stdout 不是合法 UTF-8 | 视为失败，丢弃输出 |
| 退出码非 0 | 记录警告，丢弃 stdout 中的动作 |
| stdout 动作 JSON 非法 | 记录错误，丢弃整个动作组 |
| stderr 有输出 | 失败时按 WARNING 记录，成功时按 DEBUG 记录 |
| stderr 超过 16 KiB | 截断并追加 `[truncated]` |

单个程序失败不会阻止同一钩子的其他程序。

## 下一步

- 程序能返回什么：[动作](04-actions.md)
- 配置过滤和路径：[配置](02-config.md)
- 为什么消息没有触发：[故障排查](06-troubleshooting.md)
