# bottg

一个只管理**单个 Telegram 超级群组**的机器人。它负责连接 Telegram、接收事件、执行动作。业务逻辑写在外部的“钩子程序”里，可以用任意语言实现。

钩子程序从 stdin 读取事件 JSON，向 stdout 输出动作 JSON。bottg 不限制你用什么语言，也不把业务状态留在自己进程里。

```text
Telegram  ->  bottg  ->  钩子程序（stdin JSON）
                              |
                         动作 JSON（stdout）
                              |
                         动作队列  ->  Telegram
```

## 适合用来做什么

- 在超级群组或话题群组里自动回复、清理消息、贴表情、管理话题。
- 接收图片、文件等附件，读取元数据或下载后处理，再用 `send_photo` / `send_document` 发回。
- 把业务逻辑拆成独立脚本，bot 重启不影响业务代码。
- 一个 bot 只服务一个群组，避免误操作其他聊天。

## 快速开始

需要 Python 3.15.2、[uv](https://docs.astral.sh/uv/)、一个 Telegram bot token，以及一个超级群组。

```bash
uv sync

cp config.example.json5 config.json5
# 编辑 config.json5，把 telegram.group_id 改成你的群组 ID

export BOTTG_TOKEN='123456789:AA...'
uv run bottg
```

示例配置自带六个演示程序。启动后，在群里发送：

```text
!echo 你好
```

机器人会回复“你好”。

也可以给一张图片或文件加上 caption：

```text
!attachmentinfo
```

机器人会回复该附件的类型、文件 ID、大小等信息。

> 如果普通消息没有反应，通常是 bot 的 privacy mode 未关闭，或 bot 不是群管理员。见[安装与首次启动](docs/01-install.md#准备目标群组)。

## 基本概念

| 概念 | 说明 |
| --- | --- |
| 钩子 | bottg 触发的时机：`startup`、`message`、`edited_message`、`shutdown`。 |
| 钩子程序 | `config.json5` 的 `programs` 中声明的外部命令。按配置顺序执行。 |
| 事件 | bottg 写入程序 stdin 的 JSON，包含群组、话题、消息，以及 `message.attachments` 附件元数据。 |
| 动作 | 程序通过 stdout 返回的 JSON，告诉 bottg 要做什么。 |
| 动作组 | 一次输出中的一组动作，整体入队，按数组顺序执行。 |

支持的动作：

| 动作 | 用途 |
| --- | --- |
| `send_message` | 发送文本消息 |
| `send_photo` | 发送图片（文件 ID、URL 或本地文件） |
| `send_document` | 发送任意附件（文件 ID、URL 或本地文件） |
| `delete_message` | 删除消息 |
| `set_reaction` | 给消息贴一个 emoji |
| `create_topic` | 创建话题 |
| `edit_topic` | 修改话题名称 |
| `delete_topic` | 删除话题及其所有消息 |
| `sleep` | 延迟动作队列 |

## 最小配置

```json5
{
  telegram: {
    group_id: -1001234567890, // 必填，你的超级群组 ID
  },
  programs: {
    echo: {
      command: ["python3", "examples/echo_hook.py"],
      hooks: ["message"],
    },
  },
}
```

token 推荐通过 `BOTTG_TOKEN` 环境变量提供。

## 示例命令

示例配置启用以下程序，发送对应消息即可体验：

| 命令 | 效果 |
| --- | --- |
| `!echo 文本` / `/echo 文本` | 回复文本 |
| `!slowecho 文本` | 等 1 秒后回复 |
| `!react 🔥` | 给触发消息贴表情 |
| `!delete` | 删除触发消息 |
| `!newtopic 名称` | 创建话题 |
| `!renametopic 新名称` | 改名当前话题 |
| `!deletetopic confirm` | 删除当前话题 |
| `!topicid` | 回复当前话题 ID |
| `!repost [标题]` | 用图片/文件 caption 中的命令重新发送附件 |
| `!attachmentinfo` | 回复附件的元数据 |

更多说明见[示例程序](docs/07-examples.md)。

## 文档

| 文档 | 内容 |
| --- | --- |
| [文档索引](docs/README.md) | 全部用户文档 |
| [安装与首次启动](docs/01-install.md) | 依赖、群组准备、启动、停止 |
| [配置](docs/02-config.md) | `config.json5` 全部字段与校验规则 |
| [钩子](docs/03-hooks.md) | 触发时机、事件格式、过滤规则 |
| [动作](docs/04-actions.md) | stdout 协议与每个动作的字段 |
| [运行行为](docs/05-operations.md) | 生命周期、队列、日志、退出码、限制 |
| [故障排查](docs/06-troubleshooting.md) | 消息不触发、话题动作失败等 |
| [示例程序](docs/07-examples.md) | 每个示例的用途和手动测试方法 |

## 当前版本不支持的

- 发送投票、位置、联系人等非媒体消息。
- 以内联方式发送视频、音频、语音或贴纸（可以用 `send_document` 作为文件发送）。
- 编辑消息内容、转发、置顶消息（消息编辑会触发 `edited_message`，但 bottg 不修改消息）。
- 多群组、Webhook、数据库或动作持久化。
- 把动作执行结果返回给钩子程序。

完整列表见[运行行为](docs/05-operations.md#当前版本明确不做的事)。
