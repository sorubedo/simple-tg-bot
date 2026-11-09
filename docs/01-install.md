# 安装与首次启动

## 环境要求

| 项目 | 要求 |
| --- | --- |
| Python | 3.15.2（见 `.python-version`） |
| 包管理器 | [uv](https://docs.astral.sh/uv/) |
| Telegram | 一个 bot token，一个超级群组 |
| 群组类型 | 普通群组需要先升级为超级群组；话题功能要求开启 Topics |

## 安装

```bash
git clone <仓库地址> bottg
cd bottg
uv sync
```

`uv sync` 会按 `uv.lock` 安装运行依赖和测试依赖，并准备 Python 3.15.2 环境。

## 准备目标群组

1. **把 bot 加入群组**。私有超级群组至少需要 bot 是成员。

2. **取得群组数字 ID**。先停止正在运行的 bottg，在群里发一条普通消息，然后请求：

   ```bash
   curl -s "https://api.telegram.org/bot${BOTTG_TOKEN}/getUpdates" \
     | python3 -m json.tool
   ```

   在返回的 `result[].message.chat.id` 中找到 `-100...` 形式的 ID，填入 `telegram.group_id`。

3. **开启 Topics（可选）**。需要创建、改名、删除话题时，在群组设置中开启 Topics。启动时 bottg 会检查群组是否为超级群组，并报告 `is_forum`。没有开启 Topics 只会警告，不会阻止启动，但话题动作会失败。

4. **授予权限**。删除别人的消息、管理话题通常需要管理员权限。贴表情需要群组允许对应反应。Telegram 日志通道也需要发送消息的权限。

5. **处理 privacy mode**。BotFather 开启 privacy mode 且 bot 不是管理员时，bot 只会收到命令、提及、回复和它自己发送的消息，普通消息不会到达 `message` 钩子。两种解决方式：

   - 把 bot 设为群管理员。
   - 在 BotFather 中关闭 privacy mode，然后重启 bottg。

   bottg 启动时会查询 bot 的 privacy mode 和管理员状态，两者都不满足时记录警告。

6. **避免和其他 webhook 共用 bot**。长轮询和 webhook 互斥。bottg 开始轮询时会自动调用 `deleteWebhook`，接管该 bot 的更新获取方式。如果这个 token 同时被其他服务使用，请为 bottg 使用独立 token。

## 配置

```bash
cp config.example.json5 config.json5
```

至少修改：

```json5
{
  telegram: {
    group_id: -1001234567890, // 改成你的群组 ID
  },
}
```

token 推荐只放在环境变量里：

```bash
export BOTTG_TOKEN='123456789:AA...'
```

`BOTTG_TOKEN` 的优先级高于 `telegram.token`。字段详解见[配置](02-config.md)。

## 启动

```bash
uv run bottg
```

指定其他配置文件：

```bash
uv run bottg --config /path/to/config.json5

# 或
BOTTG_CONFIG=/path/to/config.json5 uv run bottg
```

其他命令：

```bash
uv run bottg --version
uv run bottg --help
uv run python -m bottg --help
```

启动成功后，日志中会出现类似内容：

```text
bottg 0.3.0 initialized | target supergroup 'Ops' (id=-1001234567890 @ops_group) | topics=on | programs=6 | long polling started (timeout=30s)
```

示例配置启用了 `lifecycle` 程序，它会在启动时向 General 话题发送一条上线消息。看到这条消息说明 Telegram 连接和动作队列都正常。

## 验证

在群里发送：

```text
!echo 你好
```

如果收到“你好”，说明钩子和动作链路已经打通。其余示例见[示例程序](07-examples.md)。

## 停止

按 `Ctrl+C`，或向进程发送 `SIGTERM`。bottg 会：

1. 停止接收新更新。
2. 执行 `shutdown` 钩子。
3. 在 `actions.drain_timeout_seconds` 内排空动作队列。
4. 关闭 Telegram 连接和日志通道。

退出码见[运行行为](05-operations.md#退出码)。

## 下一步

- 写自己的钩子程序：[钩子](03-hooks.md)、[动作](04-actions.md)
- 调整队列、日志和轮询：[配置](02-config.md)
- 启动失败或没有反应：[故障排查](06-troubleshooting.md)
