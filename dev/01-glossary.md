# 术语表

本文件统一代码、文档和讨论中的用词。出现歧义时以这里为准。

## 核心对象

| 术语 | 含义 | 不要混用 |
| --- | --- | --- |
| bottg | 本仓库的主程序。负责 Telegram 连接、更新接收、钩子调度和动作执行。 | 不要把业务逻辑说成 bottg 的一部分。 |
| 目标群组 / target group | `telegram.group_id` 指定的唯一超级群组。所有动作只能作用于它。 | 不要用它指代其他聊天。 |
| 超级群组 / supergroup | Telegram 的聊天类型，ID 为负数，通常以 `-100` 开头。 | 不要和普通群组、频道混用。 |
| 论坛群组 / forum | 开启 Topics 的超级群组。 | 没开启 Topics 也可能仍是超级群组。 |
| General | 论坛群组的默认话题。没有 `message_thread_id`，在协议中用 `null` 表示。 | General 不是一个可以用 `topic_id` 操作的话题。 |
| 话题 / topic | 论坛群组中的讨论串。代码中话题 ID 叫 `topic_id`，消息字段叫 `message_thread_id`。 | 不要用“线程”“板块”等词指同一个东西。 |
| 话题 ID / `topic_id` | 话题的数值 ID，等于消息的 `message_thread_id`。 | 不是消息 ID。 |
| 消息 ID / `message_id` | 单条消息的数值 ID。 | 不是话题 ID。 |

## 钩子与程序

| 术语 | 含义 | 不要混用 |
| --- | --- | --- |
| 钩子 / hook | bottg 调用程序的时机，只有 `startup`、`message`、`edited_message`、`shutdown` 四种。 | 不要把“钩子”说成程序本身。 |
| 钩子程序 / program | `programs` 中配置的外部命令。每次触发启动一个新进程。 | 不要叫“插件”“扩展”“handler”。 |
| 外挂程序 | 旧文档对钩子程序的称呼，含义相同。新文档统一用“钩子程序”。 | 不要引入新的同义词。 |
| 事件 / hook event | bottg 写到程序 stdin 的 JSON，协议为 `bottg.hook/1`。 | 不是 Telegram 原始 Update。 |
| 动作 / action | 程序写到 stdout 的 JSON 指令，协议为 `bottg.actions/1`。 | 不是事件。 |
| 动作组 / action batch | 一次 stdout 解析出的动作列表，整体入队。 | 不是单个动作。 |
| 动作队列 / action queue | 只有一个 worker 的 FIFO 队列，串行执行动作组。 | 不是更新队列。 |
| 更新队列 / update queue | PTB 内部的未处理更新队列，由 `polling.max_pending_updates` 限制。 | 不是动作队列。 |

## Telegram 概念

| 术语 | 含义 |
| --- | --- |
| 更新 / update | Telegram 推送的一次变化，可能包含消息、编辑消息、回调查询等。只有消息类更新会进入钩子。 |
| `update_id` | Telegram 给更新的编号，可用于去重。启动和关闭钩子没有这个值。 |
| `allowed_updates` | 向 Telegram 订阅的更新类型集合。`null` 表示默认集合。 |
| 长轮询 / long polling | 通过 `getUpdates` 持续拉取更新。和 webhook 互斥。 |
| privacy mode | BotFather 的隐私设置。开启且 bot 不是管理员时，普通群消息不会到达 bot。 |
| 发送者 / sender | 消息的 `from_user` 或 `sender_chat`。匿名管理员和频道身份是 `type: "chat"`。 |
| 附件 / attachment | 消息携带的媒体，例如图片、文件、语音。事件中在 `message.attachments`，不是单独的钩子。 |
| 媒体组 / media group | Telegram 相册。多张图片是独立消息，共享 `message.media_group_id`。 |
| 文件来源 / file source | 出站动作中 `photo` / `document` 的取值：`file_id`、`url` 或本机 `path`。 |
| `file_id` | Telegram 文件 ID。同一 bot 可重复使用，用于免上传转发或发送。 |
| `RetryAfter` | Telegram 限流错误，带建议等待秒数。 |

## 行为与边界

| 术语 | 含义 |
| --- | --- |
| 无状态 / stateless | bottg 不持久化业务状态、动作队列或更新 offset。内存中的运行状态不算持久化状态。 |
| 背压 / backpressure | 消费者慢时让生产者等待，而不是无限缓冲。这里体现在有界更新队列和有界动作队列。 |
| 入队 / enqueue | 把整个动作组放进动作队列。失败时整组拒绝，不会部分入队。 |
| 排空 / drain | 关闭时在超时时间内执行完已入队的动作。 |
| 丢弃 / drop | 拒绝或放弃一组动作。日志会记录原因。 |
| 失败隔离 | 一个程序失败、超时或输出非法，只影响它自己，不阻止其他程序或主循环。 |
| 全有或全无 | 动作 JSON 解析和入队都是原子的。一个字段非法，整组不动。 |
| 协议版本 | `bottg.hook/1` 和 `bottg.actions/1`。不兼容变更必须提升版本号。 |
| UTF-16 码元 | Telegram 计算消息长度的单位。一个常见 emoji 通常占 2 个码元。 |

## 容易混淆的说法

- “消息”通常指 Telegram `Message`。如果想说“一次更新”，请写“更新”。
- “话题 ID”和“消息 ID”都是正整数，但含义不同。动作里分别叫 `topic_id` 和 `message_id`。
- “配置”只指 `config.json5`。“事件”是 stdin，“动作”是 stdout。
- “支持某功能”表示代码已实现、测试已覆盖、用户文档已说明。计划中的功能不算支持。
- “默认值”指省略配置项时的行为，不是 Telegram 服务端的默认值。
- “删除话题”会删除话题内所有消息，不可恢复。
- “重启”不会恢复动作队列，也不会恢复内存中的更新 offset。

## 相关文档

- 为什么这样设计：[设计取舍](02-design-decisions.md)
- 改代码前读什么：[开发约定](03-development-conventions.md)
