# 动作

程序通过 stdout 告诉 bottg 要执行什么。stdout 是动作协议的唯一出口，诊断信息必须写到 stderr。

## 基本规则

- stdout 可以为空。**退出码 0 且 stdout 为空表示成功且没有动作。** 不需要输出 `noop` 或空数组。
- stdout 必须是一个 UTF-8 JSON 文档，不是 JSON Lines，也不接受 JSON5 注释或尾逗号。
- 退出码非 0、超时、stdout 超过 `max_output_bytes`、stdout 不是合法 UTF-8、stdout 不是合法动作 JSON，都会导致本次调用的动作全部丢弃。
- 动作 JSON 是“全有或全无”：只要有一个字段非法，整个输出都不会入队。
- 动作按数组顺序执行。动作组作为一个整体入队，不会只入队一半。
- 单个动作执行失败不会阻止同组后续动作。失败会记录日志，队列继续。
- 动作是异步执行的。程序退出后不会收到执行结果，需要结果时查看日志或使用外部状态。
- 动作协议中没有 `chat_id` 或 `group_id`。所有动作只能作用于配置的目标超级群组。

## 支持的三种形式

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

`protocol` 是可选的。如果提供，目前只接受 `bottg.actions/1`。带 `actions` 的对象不能出现其他字段。

## 上下文默认值

动作会从触发钩子继承上下文，程序通常不需要关心群组和话题：

- 在 `message` / `edited_message` 中，省略 `topic_id` 时使用触发消息所在话题，省略 `message_id` 时使用触发消息 ID。
- 在 `startup` / `shutdown` 中，`topic_id` 默认为 `null`（General），`message_id` 没有默认值。
- `send_message`、`send_photo`、`send_document` 显式写 `"topic_id": null` 表示强制发到 General，即使触发消息在某个话题中。
- `send_photo` 和 `send_document` 的相对 `path` 以钩子程序的 `cwd` 为基准解析。
- `delete_message` 和 `set_reaction` 在 `startup` / `shutdown` 中必须显式提供 `message_id`。
- `delete_topic` 和 `edit_topic` 在消息钩子中默认使用触发话题。在 General、`startup`、`shutdown` 中必须显式提供 `topic_id`。
- `create_topic` 不需要话题上下文。

例如在触发消息所在话题回复，并引用触发消息：

```json
{
  "action": "send_message",
  "text": "收到",
  "reply_to_message_id": 123
}
```

`reply_to_message_id` 省略或为 `null` 表示不引用。

## `send_message`

发送一条文本消息。

| 字段 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `action` | string | 是 | | 固定为 `send_message` |
| `text` | string | 是 | | 非空，最多 4096 个 UTF-16 码元 |
| `topic_id` | integer / null | 否 | 触发话题 | 正整数，`null` 表示 General |
| `reply_to_message_id` | integer / null | 否 | `null` | 引用的消息 ID |
| `parse_mode` | string / null | 否 | `null` | `HTML`、`Markdown` 或 `MarkdownV2`，大小写不敏感 |
| `disable_notification` | boolean | 否 | `false` | 静默发送 |
| `disable_web_page_preview` | boolean | 否 | `false` | 关闭链接预览 |

## `send_photo`

发送一张图片。`photo` 可以是文件 ID、HTTP(S) URL，或本机文件路径。

| 字段 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `action` | string | 是 | | 固定为 `send_photo` |
| `photo` | string / object | 是 | | 文件来源，见下 |
| `caption` | string / null | 否 | `null` | 非空，最多 1024 个 UTF-16 码元 |
| `topic_id` | integer / null | 否 | 触发话题 | 正整数，`null` 表示 General |
| `reply_to_message_id` | integer / null | 否 | `null` | 引用的消息 ID |
| `parse_mode` | string / null | 否 | `null` | 只作用于 `caption` |
| `disable_notification` | boolean | 否 | `false` | 静默发送 |
| `has_spoiler` | boolean | 否 | `false` | 发送带剧透遮罩的图片 |

## `send_document`

以文件形式发送任意附件，不经过图片压缩。

| 字段 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `action` | string | 是 | | 固定为 `send_document` |
| `document` | string / object | 是 | | 文件来源，见下 |
| `caption` | string / null | 否 | `null` | 非空，最多 1024 个 UTF-16 码元 |
| `topic_id` | integer / null | 否 | 触发话题 | 正整数，`null` 表示 General |
| `reply_to_message_id` | integer / null | 否 | `null` | 引用的消息 ID |
| `parse_mode` | string / null | 否 | `null` | 只作用于 `caption` |
| `disable_notification` | boolean | 否 | `false` | 静默发送 |

### 文件来源

`photo` / `document` 支持以下形式：

1. 字符串：Telegram 文件 ID，或 `http://` / `https://` 开头的 URL。
2. `{"file_id": "..."}`：显式文件 ID。可以直接使用事件中 `message.attachments[].file_id`，不必重新上传。
3. `{"url": "https://..."}`：显式 HTTP(S) URL。
4. `{"path": "relative/or/absolute/file"}`：本机文件路径。相对路径按程序的 `cwd` 解析，不按 bottg 进程当前目录解析。文件在动作执行时必须存在且可读。

对象形式必须且只能包含 `file_id`、`url`、`path` 中的一个键。普通字符串不会被当作本机路径，避免和文件 ID 混淆。

动作 JSON 不承载文件内容，也不接受 base64。程序生成的文件必须先写到磁盘，再用 `path` 引用；也可以上传到可访问的 URL 后用 `url`。

`path` 指向的文件不存在或不是普通文件时，该动作失败并记录日志，队列继续执行同组后续动作。

如果路径来自事件中的 `message.attachments[].local_path`，注意这个临时文件在程序退出时就会删除，而动作在程序退出后才执行。不要把 `local_path` 直接放进动作；需要回传原文件时使用 `file_id`，需要发送处理结果时把文件写到程序自己管理的目录。

```json
{ "action": "send_photo", "photo": "AgACAgUAAxkBAAI..." }
```

```json
{
  "action": "send_photo",
  "photo": { "url": "https://example.com/cat.jpg" },
  "caption": "看这张图"
}
```

```json
{
  "action": "send_document",
  "document": { "path": "output/report.pdf" },
  "caption": "日报",
  "reply_to_message_id": 123
}
```

## `delete_message`

删除目标群组中的一条消息。

| 字段 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `action` | string | 是 | | 固定为 `delete_message` |
| `message_id` | integer | 否 | 触发消息 | 正整数。没有触发消息时必须提供 |

删除别人的消息通常需要管理员权限。

## `set_reaction`

给一条消息贴一个标准 emoji 表情。每次只设置一个反应。

| 字段 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `action` | string | 是 | | 固定为 `set_reaction` |
| `message_id` | integer | 否 | 触发消息 | 正整数。没有触发消息时必须提供 |
| `emoji` | string | 是 | | 非空标准 emoji，不支持自定义 emoji |
| `is_big` | boolean | 否 | `false` | 是否使用大号动画 |

## `create_topic`

在目标群组中创建一个 forum 话题。群组必须开启 Topics，bot 需要有管理话题的权限。

| 字段 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `action` | string | 是 | | 固定为 `create_topic` |
| `name` | string | 是 | | 非空，最多 128 个字符 |

创建成功时，bottg 会在日志中记录新话题的 `topic_id`。结果不会返回给程序。程序如果需要话题列表或名称到 ID 的映射，必须自己保存。

## `delete_topic`

删除一个 forum 话题及其中的所有消息。这个操作不可恢复。

| 字段 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `action` | string | 是 | | 固定为 `delete_topic` |
| `topic_id` | integer | 否 | 触发话题 | 正整数。General 或没有触发话题时必须提供 |

General 话题不能被删除。删除话题通常需要管理员权限。

## `edit_topic`

修改一个 forum 话题的名称。

| 字段 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `action` | string | 是 | | 固定为 `edit_topic` |
| `topic_id` | integer | 否 | 触发话题 | 正整数。General 或没有触发话题时必须提供 |
| `name` | string | 是 | | 非空，最多 128 个字符 |

General 话题不能被改名。修改话题名通常需要管理员权限。

### 话题动作示例

```json
{ "action": "create_topic", "name": "公告" }
```

```json
{ "action": "edit_topic", "topic_id": 123, "name": "新公告" }
```

```json
{ "action": "delete_topic", "topic_id": 123 }
```

`edit_topic` / `delete_topic` 省略 `topic_id` 时使用触发消息所在话题。`create_topic` 创建的新话题 ID 不会返回给程序，后续动作要操作这个新话题时，需要显式提供已知的 `topic_id`。

## `sleep`

暂停动作队列。它只延迟后续动作，不延迟同一钩子中下一个程序的启动，也不阻塞 Telegram 更新接收本身。

| 字段 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `action` | string | 是 | | 固定为 `sleep` |
| `seconds` | number | 是 | | 大于 0，最大 3600 |

## 动作错误

以下情况会被拒绝，整个动作组不会入队：

- 未知的 `action`。
- 动作对象中出现未知字段，例如 `chat_id`。
- 字段类型错误，例如把 `message_id` 写成字符串。
- `topic_id` 或 `message_id` 不是正整数。
- `text` 为空或超过长度限制。
- `caption` 为空或超过 1024 个 UTF-16 码元。
- `send_photo` / `send_document` 缺少文件来源，或文件来源对象不是恰好包含一个键。
- 文件来源对象包含未知键，或 `url` 不是 `http://` / `https://` 开头。
- 文件来源为空字符串，或 `path`、`file_id`、`url` 不是非空字符串。
- `send_document` 中出现 `send_photo` 专属的 `has_spoiler`。
- 话题 `name` 为空或超过 128 个字符。
- `parse_mode` 不在允许列表中。
- `sleep.seconds` 小于等于 0、超过 3600 或不是有限数字。
- 在启动/关闭钩子中省略了 `delete_message.message_id` 或 `set_reaction.message_id`。
- 在 General 或启动/关闭钩子中省略了 `delete_topic.topic_id` 或 `edit_topic.topic_id`。
- 话题动作中出现未知字段，例如 `create_topic` 中的 `topic_id`。

## 相关文档

- stdout 为空或程序失败时会发生什么：[钩子](03-hooks.md#错误与输出限制)
- 队列顺序、限流和重试：[运行行为](05-operations.md#动作队列)
- 用示例程序验证动作：[示例程序](07-examples.md)
