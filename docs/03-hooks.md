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

图片、文件、语音等媒体消息仍然是普通消息，会触发 `message` / `edited_message`，不需要新增钩子。媒体内容放在 `message.attachments` 数组中，文本消息的该字段为空数组。相册中的每一张图片是独立消息，共享同一个 `message.media_group_id`。媒体消息的 `text` 通常为 `null`，命令一般写在 `caption` 里，程序应同时检查两者。

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
    "attachments": [],
    "media_group_id": null,
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
    "attachments": [],
    "media_group_id": null,
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

### 图片消息示例

````json
{
  "protocol": "bottg.hook/1",
  "hook": "message",
  "program": "vision",
  "bottg": { "version": "0.3.0" },
  "group": {
    "id": -1001234567890,
    "title": "Ops",
    "username": "ops_group",
    "is_forum": true
  },
  "topic": { "id": 17 },
  "message": {
    "id": 124,
    "date": "2026-11-09T12:01:00+00:00",
    "edit_date": null,
    "text": null,
    "caption": "这张图里是什么？",
    "attachments": [
      {
        "type": "photo",
        "file_id": "AgACAgUAAxkBAAI...",
        "file_unique_id": "AQAD...",
        "file_name": null,
        "mime_type": "image/jpeg",
        "file_size": 123456,
        "width": 1280,
        "height": 720,
        "duration": null,
        "local_path": null,
        "download_error": null
      }
    ],
    "media_group_id": null,
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
  "update_id": 987654323,
  "reason": null
}
````

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
| `message.attachments` | 消息中的媒体附件。文本消息为空数组。 |
| `message.media_group_id` | 相册（媒体组）ID。不是相册的一部分时为 `null`。 |
| `message.sender` | 有效发送者，可能是用户、匿名管理员或频道身份。 |
| `message.sender.type` | `"user"` 或 `"chat"`。`"chat"` 表示匿名管理员或频道身份。 |
| `message.sender.is_bot` | 仅 `type` 为 `"user"` 时存在。 |
| `message.reply_to_message_id` | 被回复消息的 ID，不是回复则为 `null`。 |
| `message.message_thread_id` | 与 `topic.id` 相同。 |
| `message.is_topic_message` | Telegram Bot API 的 `is_topic_message`。 |
| `update_id` | Telegram 更新 ID。启动和关闭钩子为 `null`。 |
| `reason` | 关闭原因，其他钩子为 `null`。 |

`message` 可能是服务消息，例如成员加入、标题变更。这类消息的 `text` 和 `caption` 都可能为 `null`，程序应自行判断。

### 附件字段

`message.attachments` 中每一项的结构：

| 字段 | 说明 |
| --- | --- |
| `type` | `photo`、`document`、`video`、`audio`、`voice`、`video_note`、`animation` 或 `sticker`。 |
| `file_id` | Telegram 文件 ID。可以直接放进 `send_photo` / `send_document` 的 `photo` / `document` 字段，无需重新上传。 |
| `file_unique_id` | Telegram 的稳定文件标识，同一文件在不同 bot 之间相同。 |
| `file_name` | 原始文件名，没有则为 `null`。照片、语音等通常没有。 |
| `mime_type` | MIME 类型，Telegram 没有提供时为 `null`。照片固定为 `image/jpeg`。 |
| `file_size` | 文件字节数，Telegram 没有提供时为 `null`。 |
| `width` / `height` | 图片、视频、贴纸的像素尺寸，不适用时为 `null`。 |
| `duration` | 音视频时长（秒），不适用时为 `null`。 |
| `local_path` | 下载成功时为临时文件的绝对路径，否则为 `null`，见下。 |
| `download_error` | 下载失败原因。未开启下载时恒为 `null`。 |

照片消息在 Telegram 中带有多个尺寸。bottg 只暴露像素面积（`width * height`）最大的那个，面积相同时取 `file_size` 更大的，避免程序误以为一条消息有多个附件。`animation` 消息在 Bot API 中同时带有 `document` 字段，bottg 只报告一次 `animation`。

### 下载附件

默认只传元数据，不下载文件。需要在钩子程序里读取文件内容时，在配置中开启：

```json5
{
  message: {
    download_attachments: true,
    max_attachment_bytes: 20000000,
  },
}
```

开启后，bottg 会在启动程序前把附件下载到本次调用专属的临时目录，并把绝对路径写入 `local_path`。程序退出后临时目录会被删除，需要保留时由程序自己复制到别处。不要把 `local_path` 直接放进动作：动作在程序退出后才执行，那时文件已经被删除。需要回传原文件时直接使用 `file_id`。下载使用程序的 `timeout_seconds`，`getFile` 和实际下载各自最多等待这么久。

单个文件超过 `max_attachment_bytes`、下载超时或 Telegram 返回错误时，`local_path` 保持 `null`，`download_error` 为以下值之一：

| `download_error` | 含义 |
| --- | --- |
| `too_large` | 文件超过 `max_attachment_bytes`，或下载后发现超过限制。 |
| `timeout` | `getFile` 或下载超时。 |
| `telegram_error` | Telegram API 拒绝下载。 |
| `io_error` | 写入临时文件失败。 |
| `invalid_file_id` | 事件中的 `file_id` 为空或类型错误。 |
| `unavailable` | 程序要求下载，但 bottg 没有可用的媒体客户端。 |
| `failed` | 其他未预期错误。 |

单个附件下载失败不会跳过程序，程序仍然会收到事件并自行决定如何处理。下载发生在消息钩子的串行处理链上，慢下载会延迟后续消息。

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
