# 故障排查

先看 stdout 日志。把 `runtime.log_level` 调到 `"DEBUG"` 可以看到钩子触发、程序过滤和动作执行的细节。

```json5
{
  runtime: {
    log_level: "DEBUG",
  },
}
```

## 启动失败

| 现象 | 检查 |
| --- | --- |
| 退出码 `2` | 配置文件路径、JSON5 语法、必填字段。错误信息会指出具体键。 |
| `cannot read ...` | `--config` / `SIMPLE_TG_BOT_CONFIG` 指向的文件是否存在，权限是否可读。 |
| `Telegram bot token is missing` | 设置 `SIMPLE_TG_BOT_TOKEN`，或在 `telegram.token` 中填写。 |
| `invalid format` | token 必须形如 `123456789:AA...`。 |
| `Telegram rejected the configured bot token` | token 是否复制完整，是否已在 BotFather 重置。 |
| `cannot access Telegram group` | bot 是否已加入群组，`group_id` 是否正确，bot 是否被踢出。 |
| `not a supergroup` | 目标必须是超级群组。普通群组需要先升级。 |
| 启动警告 `does not have Topics enabled` | 群组未开启 Topics。只影响话题动作，不影响普通消息。 |

## 消息钩子不触发

按顺序检查：

1. **privacy mode 和管理员状态**。BotFather 开启 privacy mode 且 bot 不是管理员时，bot 只会收到命令、提及、回复和它自己发送的消息。把 bot 设为群管理员，或在 BotFather 关闭 privacy mode，然后重启 Simple-TG-Bot。启动日志会给出警告。

2. **`allowed_updates`**。如果配置显式写了数组，必须包含 `"message"` 或 `"edited_message"`。删除该项或写成 `null` 可恢复 Telegram 默认集合。启动时如果缺少对应类型，会记录警告。

3. **发送者**。`message.ignore_bots` 默认 `true`，其他 bot 用户发送的消息会被忽略。用另一个 bot 测试时改成 `false`。

4. **目标群组**。消息必须发在 `telegram.group_id` 对应的超级群组里。私聊、其他群组和频道不会触发。

5. **消息类型**。`message` 只处理新消息。编辑消息需要订阅 `edited_message`。频道帖子、回调查询不会触发。服务消息可能触发，但 `text` / `caption` 可能为 `null`。图片和文件消息会触发，但命令通常在 `caption` 而不是 `text` 中。

6. **编辑事件的内容**。`edited_message` 只有编辑后的内容，没有编辑前的版本。

7. **命令格式**。示例脚本只响应以 `!echo ` 或 `/echo ` 开头的消息，注意 `echo` 后面有一个空格：

   ```text
   !echo 你好
   /echo 你好
   /echo@你的bot用户名 你好
   ```

   在 privacy mode 下，`/` 命令比 `!` 命令更可靠。

8. **程序路径**。`command` 的相对路径按 `cwd` 解析，`cwd` 默认是配置文件所在目录。启动命令失败、超时或 stdout 非法都会出现在日志中。

9. **提高日志等级**。`runtime.log_level` 改为 `"DEBUG"` 后，确认是否出现：

   ```text
   message hook trigger: message_id=... chat_id=... thread_id=...
   edited_message hook trigger: ...
   ```

   没有这行说明更新没有到达处理器。有这行但没有程序日志，检查 `hooks`、`message.topics`、`ignore_bots`。

10. **验证动作链路**。示例 `lifecycle` 程序会在启动时向 General 发送一条消息。如果它也没有出现，优先检查 bot 的发送权限、privacy mode 和日志通道。

## 程序运行了，但动作没生效

| 现象 | 可能原因 |
| --- | --- |
| 日志说 `produced no actions` | stdout 为空。退出码 0 且 stdout 为空是正常的“不做任何事”。 |
| 日志说 `invalid action JSON` | stdout 混入了诊断信息、日志或非 JSON 文本。诊断必须写 stderr。 |
| 日志说 `exited with status N` | 程序退出码非 0，stdout 中的动作被丢弃。 |
| 日志说 `timed out` | 程序超过 `timeout_seconds`，进程被杀死。 |
| 日志说 `stdout exceeded` | 输出超过 `max_output_bytes`。 |
| 日志说 `actions ... were dropped` | 动作队列已满或已关闭。检查 `actions.max_pending` 和慢程序。 |
| 日志说 `unknown field` | 动作里出现了不支持的字段，例如 `chat_id`。整个动作组会被丢弃。 |
| 日志说 `message_id is required` | 在 `startup` / `shutdown` 中使用了 `delete_message` 或 `set_reaction`，但没有提供 `message_id`。 |
| 日志说 `topic_id is required` | 在 General 或启动/关闭钩子中使用了 `delete_topic` / `edit_topic`，但没有提供 `topic_id`。 |

手动验证程序输出：

```bash
printf '%s\n' '{"message":{"id":5,"text":"!echo hello"}}' \
  | uv run python examples/echo_hook.py
```

期望输出：

```json
{"action": "send_message", "text": "hello", "reply_to_message_id": 5}
```

## 动作执行失败

| 现象 | 检查 |
| --- | --- |
| `delete_message` 失败 | 删除别人的消息通常需要管理员权限。消息可能已经被删除。 |
| `set_reaction` 失败 | 群组是否允许该反应，emoji 是否是标准 emoji。一次只能设置一个。 |
| `create_topic` 失败 | 群组是否开启 Topics，bot 是否有管理话题权限。 |
| `edit_topic` / `delete_topic` 失败 | 话题是否存在，General 不能改名或删除，bot 是否有管理员权限。 |
| `send_photo` / `send_document` 失败 | 文件 ID 是否属于当前 bot，URL 是否可访问，`path` 是否存在且可读。普通字符串会被当作文件 ID，本机文件必须写成 `{"path": "..."}`。 |
| 附件下载失败 | 检查事件中的 `download_error`。`too_large` 调整 `max_attachment_bytes`；`timeout` 调整 `timeout_seconds`；`telegram_error` 检查 bot 是否能读取该文件。 |
| `RetryAfter` | 触发了 Telegram 限流。队列会自动等待并重试，最多 3 次。可以用 `actions.min_interval_seconds` 主动降低频率。 |
| 动作没有执行 | 查看 stdout 日志中的 `action ... failed` 或 `unexpected failure while executing`。 |

动作执行结果不会返回给程序。需要确认结果时看日志，或让程序自己保存状态。

## 话题相关

- 创建、改名、删除话题要求群组开启 Topics，bot 是管理员并有管理话题权限。
- General 话题不能改名，也不能删除。
- `delete_topic` 会删除话题内所有消息，操作不可恢复。
- `create_topic` 创建的新话题 ID 只写入日志，不会返回给程序。后续操作需要显式提供已知的 `topic_id`。
- Telegram Bot API 没有列出所有话题的接口。需要话题列表时由程序自己保存。
- 非话题群组中 `topic.id` 为 `null`，话题过滤数组里的 `null` 表示 General 或非话题群组。

## 日志通道

- Telegram 日志发送失败不会影响 bot。错误会直接写到 stdout。
- 日志太多时可能看到 `log record(s) dropped because the Telegram log buffer was full`。这是有界的保护行为。
- 如果不想在群里看到日志，把 `runtime.telegram_log_level` 设为 `null` 或 `"OFF"`。
- 日志中的 bot token 会被替换为 `<redacted>`，但不要主动把 token 写进日志。

## 重启与重复处理

`getUpdates` 的 offset 只保存在内存中。进程崩溃或重启后，尚未确认的更新可能被 Telegram 重新投递，程序可能再次运行。

动作不是幂等的。需要去重时，程序可以使用 `update_id`、`message.id` 或自己的外部状态。

## 关闭很慢

关闭时会先等待已进入队列的消息处理完，再执行 `shutdown` 钩子，最后排空动作队列。以下配置会影响最坏情况：

- `polling.max_pending_updates`：积压消息上限。
- 每个程序的 `timeout_seconds`：单个钩子最坏执行时间。
- `actions.drain_timeout_seconds`：动作排空上限。

如果关闭时日志提示 `action queue did not drain`，说明队列超时，当前动作被取消，剩余动作被丢弃。

## 还是无法定位

1. 用 `runtime.log_level: "DEBUG"` 重启。
2. 确认 stdout 中出现 `verified target group` 和 `long polling started`。
3. 手动用 `printf` 管道测试钩子程序。
4. 检查 Telegram 群组里的权限和 Topics 设置。
5. 对照[钩子](03-hooks.md)和[动作](04-actions.md)的字段表。
