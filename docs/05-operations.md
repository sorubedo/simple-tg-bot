# 运行行为

## 启动顺序

1. 解析命令行参数和 JSON5 配置。配置失败时退出码为 `2`，错误写到 stderr。
2. 初始化日志：stdout 立即启用，Telegram 通道先缓冲。
3. 创建 Telegram 应用，更新队列有界，更新严格串行处理。
4. 连接 Telegram，调用 `getChat` 读取目标群组。
5. 校验群组类型必须是 `supergroup`，否则启动失败。没有开启 Topics 只记录警告。
6. 绑定 Telegram 日志通道到已验证的群组，补发之前缓冲的日志。
7. 启动 bot 和动作队列。
8. 创建程序调度器，注册消息处理器。
9. 按配置顺序执行 `startup` 钩子。程序放入队列的动作同时开始执行。
10. 启动 `getUpdates` 长轮询。
11. 输出就绪日志。

就绪日志示例：

```text
bottg 0.3.0 initialized | target supergroup 'Ops' (id=-1001234567890 @ops_group) | topics=on | programs=6 | long polling started (timeout=30s)
```

## 关闭顺序

收到 `SIGINT` 或 `SIGTERM` 后：

1. 停止轮询。
2. 等待已经进入更新队列的消息处理完。此后不再触发新的消息钩子。
3. 按配置顺序执行 `shutdown` 钩子。此时 Telegram API 仍可用，关闭钩子可以发送、删除消息或贴表情。
4. 排空动作队列，最多等待 `actions.drain_timeout_seconds`。超时会取消当前动作并丢弃剩余动作。
5. 刷新并关闭 Telegram 日志通道。
6. 关闭 bot HTTP 客户端。

如果积压了很多消息，而消息钩子又很慢，关闭会相应变慢。可以调小 `polling.max_pending_updates` 和程序的 `timeout_seconds` 控制最坏情况。

`shutdown` 钩子的 `reason`：

- `"signal"`：由 `SIGINT` / `SIGTERM` 正常触发。
- `"error"`：启动后因异常或外部取消进入关闭流程。

## 动作队列

### 顺序与并发

- 只有一个 worker，按 FIFO 执行动作组。
- 消息更新串行处理。慢钩子会延迟后续消息，但动作不会并发执行。
- 启动钩子的动作排在长轮询开始前入队。消息动作排在它们之后。关闭钩子的动作排在已入队的消息动作之后。
- 关闭时先停止更新处理，再运行关闭钩子，因此关闭钩子的动作不会和消息钩子竞争。

### 有界队列与背压

- `actions.max_pending` 限制等待中的动作组数量，不是单个动作数量。
- 队列满时，生产者等待最多 `actions.enqueue_timeout_seconds`。仍然没有空间时，整个动作组被拒绝并记录错误。
- 动作组不会部分入队。

### 限流和重试

- `actions.min_interval_seconds` 控制两次 Telegram 动作之间的最小间隔。
- Telegram 返回 `RetryAfter` 时，队列等待指定时间后重试，最多尝试 3 次。
- 单次 `RetryAfter` 等待超过 120 秒时不再重试。
- 其他 Telegram 错误，例如消息不存在、没有删除权限、反应不可用、话题不存在，会记录警告并跳过该动作。
- 非 Telegram 异常会记录完整堆栈并跳过该动作。

### 关闭排空

- 关闭钩子执行完后，动作队列等待已入队动作完成，最多 `actions.drain_timeout_seconds`。
- 超时会取消当前动作，并丢弃剩余动作。
- `sleep` 动作可以被关闭取消。

### 语义边界

- `getUpdates` 的 offset 只保存在内存中。进程崩溃或重启后，尚未确认的更新可能被 Telegram 重新投递，程序可能再次运行。
- 动作不是幂等的。需要去重时，程序可以使用 `update_id`、`message.id` 或自己的外部状态。
- 动作队列不持久化。进程退出时未执行的动作会丢失。
- 不保存业务状态，也不保存编辑前的消息内容。

### 附件下载

- 只有配置了 `message.download_attachments: true` 的程序才会下载附件。默认只传元数据。
- 下载发生在程序启动前，属于消息钩子的串行处理链。慢下载会延迟后续消息。
- 每次程序调用使用独立的临时目录，程序退出后删除。程序需要保留文件时必须自己复制到其他位置。
- `getFile` 和实际下载各自受程序的 `timeout_seconds` 限制。
- 下载失败不会跳过程序。事件中的 `local_path` 为 `null`，`download_error` 说明原因，程序自行决定是否继续。
- `max_attachment_bytes` 限制单个文件的大小。Telegram Bot API 对 bot 下载文件本身也有约 20 MB 的上限。

## 日志

### stdout

同步输出，由 `runtime.log_level` 控制等级。配置错误仍然直接写到 stderr，因为那时日志系统还没有建立。

两个通道都会把日志文本中的 bot token 替换为 `<redacted>`。

### Telegram

`runtime.telegram_log_level` 及以上的日志会批量发送到目标群组。发送时省略 `message_thread_id`，在话题群组中即默认 General 话题，在非话题群组中发送到主聊天。

这个通道独立于更新队列和动作队列，行为如下：

- 最多缓冲 1000 条记录。队列满时丢弃最旧记录，并在下一批消息中报告丢弃数量。
- 每 2 秒聚合一批，消息之间至少间隔 3 秒，以适配群组约 20 条/分钟的限额。
- 单条消息按 UTF-16 计算不超过 4000 个码元。
- 排除 `httpx`、`httpcore` 和大部分 Telegram 库日志，避免“发送日志产生日志再发送”的循环。`telegram.ext` 的 WARNING/ERROR（例如轮询网络错误）会同步到群组。
- 被 Telegram 通道排除的日志仍然走 stdout。`httpx` / `httpcore` 的级别被单独设为 WARNING，因此它们的 INFO/DEBUG 不会输出。
- 发送失败不会影响 bot 运行。本批日志被丢弃，错误直接写到 stdout，并按指数退避重试后续批次。
- 关闭时最多等待 10 秒刷新最后的日志，超时后放弃剩余记录。

## 退出码

| 退出码 | 含义 |
| --- | --- |
| `0` | 正常停止，包括收到 `SIGINT` / `SIGTERM` 后优雅退出 |
| `1` | 启动失败、Telegram API 错误或未预期异常 |
| `2` | 配置文件缺失、JSON5 语法错误或校验失败 |
| `130` | 信号处理器安装前或平台限制下收到 `KeyboardInterrupt` |

## 当前版本明确不做的事

- 发送投票、位置、联系人等非媒体消息。
- 以内联方式发送视频、音频、语音或贴纸（可以用 `send_document` 作为文件发送）。
- 编辑消息内容（只能接收 `edited_message` 通知）、转发、置顶、复制消息。
- 自定义 emoji 反应或一次设置多个反应。
- 处理频道帖子、回调查询等非 `message` / `edited_message` 更新。
- 列出话题列表。Telegram Bot API 没有对应接口，需要程序自己保存。
- 把动作执行结果返回给钩子程序。例如 `create_topic` 创建的新话题 ID 只会写入日志。
- 设置话题图标颜色或自定义 emoji，以及删除或改名 General 话题。
- 并行执行多个钩子程序，或并行执行一个程序的多份实例。
- 持久化动作队列、更新 offset 或任何业务状态。
- 对非 `RetryAfter` 的 Telegram 错误自动重试。
- 自动重试失败的钩子程序。消息钩子只有在收到下一条匹配更新时才会再次运行。
- Shell 展开、环境变量展开或 `command` 字符串形式。
- 让动作访问目标群组之外的聊天。
- Webhook 模式。

## 相关文档

- 每个配置项：[配置](02-config.md)
- 常见故障：[故障排查](06-troubleshooting.md)
