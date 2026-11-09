# bottg 用户文档

建议按顺序阅读。日常使用只需要看“安装”和“配置”，其余作为参考。

| 文档 | 内容 |
| --- | --- |
| [01 安装与首次启动](01-install.md) | 环境要求、准备群组、启动和停止 |
| [02 配置](02-config.md) | `config.json5` 每个字段的含义和校验规则 |
| [03 钩子](03-hooks.md) | 四种钩子、stdin 事件格式、程序过滤 |
| [04 动作](04-actions.md) | stdout 动作协议、上下文默认值、全部动作 |
| [05 运行行为](05-operations.md) | 启动/关闭顺序、动作队列、日志、退出码、限制 |
| [06 故障排查](06-troubleshooting.md) | 消息不触发、动作不生效等常见问题 |
| [07 示例程序](07-examples.md) | 仓库自带示例的用法和手动测试 |

## 最短路径

1. 按[安装](01-install.md)启动 bot。
2. 按[配置](02-config.md)写好 `programs`。
3. 程序从 stdin 读事件，见[钩子](03-hooks.md)。
4. 程序向 stdout 写动作，见[动作](04-actions.md)。
5. 不生效时看[故障排查](06-troubleshooting.md)。

开发者与 AI 协作者请回到仓库根目录看 [AGENTS.md](../AGENTS.md) 和 [dev/](../dev/)。
