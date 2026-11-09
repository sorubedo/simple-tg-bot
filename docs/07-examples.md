# 示例程序

`config.example.json5` 默认启用六个示例程序，覆盖全部钩子和动作。它们的目标是演示协议，不是生产实现。

| 程序 | 钩子 | 命令 | 覆盖动作 |
| --- | --- | --- | --- |
| `echo_hook.py` | `message`、`edited_message` | `!echo 文本`、`/echo 文本`、`!slowecho 文本` | `send_message`、`sleep` |
| `reaction_hook.py` | `message` | `!react [emoji]`、`/react [emoji]` | `set_reaction` |
| `cleanup_hook.py` | `message` | `!delete`、`!delete 123`、`/delete` | `delete_message` |
| `topic_hook.py` | `message` | `!newtopic 名称`、`!renametopic 新名称`、`!deletetopic confirm`、`!topicid`、`!topics` | `create_topic`、`edit_topic`、`delete_topic`、`send_message` |
| `media_hook.py` | `message` | `!repost [标题]`、`!attachmentinfo`（写在图片/文件 caption 中） | `send_photo`、`send_document`、`send_message` |
| `lifecycle_hook.py` | `startup`、`shutdown` | 无 | `send_message` |

命令同时支持 `!` 和 `/` 前缀。`/command@botusername` 形式也支持。

## 命令说明

### `echo_hook.py`

```text
!echo 你好
/echo 你好
!slowecho 你好
```

- `!echo` 回复剩余文本，并引用触发消息。
- `!slowecho` 先返回一个 1 秒的 `sleep` 动作，再发送消息，用于演示动作数组和队列延迟。
- 把一条消息编辑成以 `!echo ` 或 `/echo ` 开头，会再次触发程序。事件中的 `message.text` 是编辑后的内容，`edit_date` 是编辑时间，编辑前的内容不会出现在 stdin 中。

### `reaction_hook.py`

```text
!react
!react 🔥
```

- 省略 emoji 时默认 `👍`。
- 动作省略 `message_id`，因此自动作用于触发消息。
- 只支持 Telegram 标准 emoji，不支持自定义 emoji。

### `cleanup_hook.py`

```text
!delete
!delete 123
```

- `!delete` 删除触发消息。
- `!delete 123` 删除指定消息。
- 删除别人的消息通常需要管理员权限。

### `topic_hook.py`

```text
!newtopic 公告
!renametopic 新公告
!deletetopic confirm
!topicid
!topics
```

- 这些命令要求群组开启 Topics，bot 有管理话题权限。
- `!renametopic` 和 `!deletetopic` 必须在具体话题内使用。General 话题不能改名或删除。
- `!deletetopic` 必须带 `confirm`，并且会删除该话题下的所有消息。
- `!topicid` 回复当前话题 ID。General 显示为 `General/无话题`。
- `!topics` 解释为什么无法列出所有话题。Telegram Bot API 没有对应接口，需要程序自己保存话题列表。
- `!newtopic` 创建的新话题 ID 只写入日志，不会返回给脚本。

### `media_hook.py`

把命令写在图片或文件的 caption 中：

```text
!repost 新标题
!attachmentinfo
```

- `!repost` 读取 `message.attachments[0].file_id`，用 `send_photo` 或 `send_document` 重新发送，不重新上传文件。
- `!attachmentinfo` 回复附件类型、文件 ID、文件名、MIME、大小和尺寸。
- 媒体消息的 `message.text` 是 `null`，命令在 `message.caption` 中；示例同时检查两者。
- 示例默认只读元数据。要读取文件内容，在程序配置里设置 `message.download_attachments: true`，然后使用 `message.attachments[0].local_path`。

### `lifecycle_hook.py`

不需要命令。程序在 `startup` 和 `shutdown` 时向 General 话题发送一条状态消息。

`shutdown` 消息中的原因可能是：

- `signal`：正常停止。
- `error`：异常关闭。

## 手动测试

示例程序都可以直接通过 stdin 测试。

echo：

```bash
printf '%s\n' '{"message":{"id":5,"text":"!echo hello"}}' \
  | uv run python examples/echo_hook.py
```

输出：

```json
{"action": "send_message", "text": "hello", "reply_to_message_id": 5}
```

reaction：

```bash
printf '%s\n' '{"message":{"id":5,"text":"!react 🔥"}}' \
  | uv run python examples/reaction_hook.py
```

输出：

```json
{"action": "set_reaction", "emoji": "🔥"}
```

cleanup：

```bash
printf '%s\n' '{"message":{"id":5,"text":"!delete"}}' \
  | uv run python examples/cleanup_hook.py
```

输出：

```json
{"action": "delete_message"}
```

topic：

```bash
printf '%s\n' '{"message":{"id":5,"text":"!newtopic 公告"},"topic":{"id":null}}' \
  | uv run python examples/topic_hook.py
```

输出：

```json
{"action": "create_topic", "name": "公告"}
```

media：

```bash
printf '%s\n' \
  '{"message":{"id":5,"text":null,"caption":"!repost 新标题","attachments":[{"type":"photo","file_id":"AgAC-photo"}]}}' \
  | uv run python examples/media_hook.py
```

输出：

```json
{"action": "send_photo", "photo": "AgAC-photo", "caption": "新标题"}
```

lifecycle：

```bash
printf '%s\n' '{"hook":"shutdown","bottg":{"version":"0.3.0"},"reason":"signal"}' \
  | uv run python examples/lifecycle_hook.py
```

输出：

```json
{"action": "send_message", "text": "bottg stopping (signal)"}
```

## 在生产中使用

- 所有 `message` 钩子程序都会在每条匹配消息上运行。示例按动作拆开是为了便于阅读。生产环境可以合并成一个程序，减少子进程启动次数。
- 示例没有状态。需要去重、话题列表或业务数据时，由程序自己持久化。
- 不要把诊断信息写到 stdout，否则动作 JSON 会解析失败。
- 程序失败不会重试。需要重试时，在下一条匹配消息中再处理，或在程序内部实现。
