# AGENTS.md

Simple-TG-Bot 是只服务单个 Telegram 超级群组的 bot。业务逻辑在外部钩子程序里：stdin 读事件 JSON，stdout 写动作 JSON。

## 文件组织

- `src/simple_tg_bot/`：主程序
- `examples/`：示例钩子程序
- `tests/`：pytest 测试
- `docs/`：用户文档
- `dev/`：开发约定、设计取舍、术语
- `config.example.json5`：配置示例

## 改之前先看

- 任何改动：`dev/03-development-conventions.md`
- 不懂为什么这样设计：`dev/02-design-decisions.md`
- 术语不确定：`dev/01-glossary.md`
- 改配置：`docs/02-config.md`
- 改钩子或事件：`docs/03-hooks.md`
- 改动作：`docs/04-actions.md`
- 改生命周期、日志：`docs/05-operations.md`
- 改示例：`docs/07-examples.md`

## 常用命令

```bash
uv run pytest
uv run simple-tg-bot --version
```
