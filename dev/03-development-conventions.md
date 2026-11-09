# 开发约定

本文件是开发者和 AI 协作者的共同规则。目标不是限制实现，而是避免无意中推翻既有设计。

## 阅读顺序

1. `AGENTS.md`：仓库是什么、文件在哪里、改什么看什么。
2. 本文件：底线、模块边界、改动清单。
3. [设计取舍](02-design-decisions.md)：为什么这样设计，哪些不能随便改。
4. [术语表](01-glossary.md)：统一用词，消除歧义。
5. 对应的用户文档和源码、测试。

## 事实来源

- **实际行为**以代码和测试为准。
- **预期行为**以 `docs/` 和 `dev/02-design-decisions.md` 为准。
- **命名和术语**以 `dev/01-glossary.md` 为准。
- 如果文档和代码冲突，不要静默选择一边。先确认哪边是错的，再同时修正代码、测试和文档。

## 不可破坏的底线

以下条件是项目定位的一部分。除非用户明确要求并同意迁移方案，否则不要改动：

1. **单群组**：一个进程只服务一个 `telegram.group_id`。
2. **动作协议没有 `chat_id` / `group_id`**。
3. **无持久化**：不加数据库、会话文件、PTB persistence、动作队列持久化。
4. **钩子程序是外部进程**：不改成导入式插件或进程内 handler。
5. **stdout 只放动作 JSON**，诊断信息走 stderr。
6. **动作组原子**：解析全有或全无，入队整组成功或整组拒绝。
7. **串行**：更新串行处理，动作队列单 worker。
8. **有界**：更新队列和动作队列都有上限，慢消费者形成背压。
9. **失败隔离**：一个程序失败不影响其他程序和主循环。
10. **启动时校验目标群组**：必须是 `supergroup`。
11. **token 脱敏**：日志中不能出现明文 token。
12. **协议版本稳定**：`simple-tg-bot.hook/1` 和 `simple-tg-bot.actions/1`，不兼容变更必须提升版本。
13. **配置严格**：未知键、错误类型、越界值直接报错。
14. **钩子只有四种**：`startup`、`message`、`edited_message`、`shutdown`。
15. **动作只有九种**：`send_message`、`send_photo`、`send_document`、`delete_message`、`set_reaction`、`create_topic`、`edit_topic`、`delete_topic`、`sleep`。

## 模块边界

| 文件 | 只负责 | 不应该做 |
| --- | --- | --- |
| `config.py` | 读取 JSON5、校验、生成不可变配置对象 | 连接 Telegram、执行程序 |
| `actions.py` | 动作模型、stdout JSON 解析、上下文默认值 | 调用 Telegram API |
| `action_queue.py` | 排队、限流、重试、执行动作 | 启动外部程序 |
| `programs.py` | 启动子进程、构造 stdin、解析退出状态和 stdout | 直接调用 Telegram API |
| `bot.py` | PTB 生命周期、目标群组校验、钩子编排、关闭顺序 | 解析配置细节、执行业务逻辑 |
| `cli.py` | 命令行参数、日志初始化、退出码 | 业务逻辑 |
| `logging_setup.py` | stdout 和 Telegram 日志通道 | 经过更新队列或动作队列 |
| `errors.py` | 异常层次 | 具体业务流程 |
| `examples/` | 演示协议 | 生产级状态管理 |

跨模块调用时保持方向：`cli -> bot -> programs/action_queue -> actions/config`。不要让底层模块反向依赖 `bot.py`。

## 改什么，先看什么

| 要改的内容 | 必读 | 通常要改的文件 | 必跑测试 |
| --- | --- | --- | --- |
| 配置字段、默认值、校验 | [配置](../docs/02-config.md)、[设计取舍](02-design-decisions.md) | `config.py`、`config.example.json5`、`docs/02-config.md` | `tests/test_config.py` |
| 新增或修改动作 | [动作](../docs/04-actions.md)、[术语表](01-glossary.md) | `actions.py`、`action_queue.py`、`docs/04-actions.md`、`README.md` 动作表 | `tests/test_actions.py`、`tests/test_action_queue.py` |
| 钩子触发、事件字段、过滤 | [钩子](../docs/03-hooks.md) | `programs.py`、`bot.py`、`docs/03-hooks.md` | `tests/test_programs.py`、`tests/test_bot.py` |
| 生命周期、关闭、日志、退出码 | [运行行为](../docs/05-operations.md) | `bot.py`、`cli.py`、`logging_setup.py`、`docs/05-operations.md` | `tests/test_bot.py`、`tests/test_logging.py` |
| 用户使用方式、安装步骤 | [安装](../docs/01-install.md)、[示例](../docs/07-examples.md) | `README.md`、`docs/01-install.md`、`docs/07-examples.md` | 手动跑 `--version` / `--help` |
| 示例程序 | [示例](../docs/07-examples.md) | `examples/*.py`、`docs/07-examples.md` | 手动 `printf` 管道测试 |
| 设计前提本身 | [设计取舍](02-design-decisions.md) | `dev/02-design-decisions.md` 及相关代码、文档 | 全量 `uv run pytest` |

## 协议演进规则

- 事件协议字符串：`simple-tg-bot.hook/1`。
- 动作协议字符串：`simple-tg-bot.actions/1`。
- 可以保持 `/1` 的变更：增加旧程序会忽略的可选事件字段；增加可选动作字段且旧解析器不需要理解它。当前动作解析器会拒绝未知字段，因此给动作加字段实际上需要同步升级解析器，不能假定旧版兼容。
- 必须提升版本的变更：删除或重命名字段、改变字段含义、增加必填字段、改变动作或钩子的语义、改变默认值导致行为不兼容。
- 提升版本时，要在 `dev/02-design-decisions.md` 写迁移方案，并更新 `docs/03-hooks.md`、`docs/04-actions.md`。
- 不要为了“以后可能用得上”加字段。协议越小越稳定。
- 不要在动作协议中增加 `chat_id`、`group_id` 或任何跨群组寻址字段。
- 不要重命名现有动作或钩子。需要新语义时增加新动作，而不是偷偷改变旧动作。

## 编码约定

- Python 3.15.2，使用 `from __future__ import annotations`。
- 公共函数和数据结构加类型标注。内部小函数也尽量加。
- 配置和协议对象使用 `@dataclass(frozen=True, slots=True)`。
- 常量用 `Final`，接口用 `Protocol`，异常继承 `SimpleTgBotError` 的对应子类。
- 代码标识符、docstring、测试名用英文。用户文档和开发者文档用中文。
- 不引入新运行时依赖，除非更新 `pyproject.toml`、`dev/02-design-decisions.md` 并说明理由。
- 不在事件循环里做阻塞 IO。子进程、网络、日志发送都必须是异步或非阻塞的。
- 子进程必须有超时、stdout 字节上限和 stderr 上限。
- 预期错误用项目异常，不要用裸 `Exception` 控制流程。
- 日志用模块级 `LOGGER`，不要在库里 `print`。只有 CLI 配置错误和日志传输警告可以直接写 stderr/stdout。
- 不要把 token、完整消息内容或用户隐私写进日志。token 会被自动替换为 `<redacted>`，但不要依赖它来兜底。
- 保持现有风格，不要顺手做无关重构。

## 测试约定

```bash
uv run pytest
uv run simple-tg-bot --version
uv run python -m simple_tg_bot --help
```

- 测试不访问真实 Telegram，不需要真实 token。
- 使用 `FakeBot` 或本地子进程验证行为。
- 修改协议、配置、队列或生命周期时，必须增加或更新对应测试。
- 测试文件按模块组织：
  - `test_config.py`：配置解析和校验。
  - `test_actions.py`：动作解析和错误。
  - `test_action_queue.py`：顺序、背压、重试、排空。
  - `test_programs.py`：子进程、stdin、stdout、stderr、超时、过滤。
  - `test_bot.py`：群组校验、生命周期、关闭顺序。
  - `test_logging.py`：日志通道、脱敏、限流、关闭刷新。
- 修 bug 时先写能复现的测试，再修。

## 文档约定

- `README.md` 只回答“这是什么、怎么用”。保持简短，细节链接到 `docs/`。
- `docs/` 面向使用者，按数字前缀排序。新增用户文档时同时更新 `docs/README.md` 的索引。
- `dev/` 面向开发者和 AI，按数字前缀排序，记录约定、取舍和术语。
- 行为变化必须同步更新相关文档。不要留下“代码改了文档没改”的状态。
- 文档中的命令、字段名、协议字符串必须和代码一致。
- 不要把设计争论写进 README。设计理由放 `dev/02-design-decisions.md`。
- 不要修改本地 `config.json5` 作为提交内容，它被 `.gitignore` 忽略。示例配置改 `config.example.json5`。

## AI 协作者额外规则

- 先读 `AGENTS.md` 和本文件，再读任务相关的 `docs/`、`dev/` 和源码。
- 不要实现 `dev/02-design-decisions.md` 中“不做的事”，除非用户明确要求。用户要求时，先更新设计文档并说明影响。
- 不要因为“更优雅”而推翻单群组、无状态、串行队列、严格配置这些前提。
- 不要静默改变协议、默认值或错误语义。行为变化要同时改测试和文档。
- 不要把示例程序改成有状态的生产实现，也不要把业务逻辑塞进 `src/simple_tg_bot`。
- 不要新增依赖、后台服务、数据库或 Webhook。确有需要时先说明理由。
- 不要删除或弱化现有校验来“让测试通过”。
- 改动尽量小，不相关的问题只记录，不顺手重构。
- 完成任务前至少运行 `uv run pytest`。涉及命令行时再跑 `--version` 和 `--help`。
- 如果代码和文档冲突，报告冲突并一起修正，不要假装没看见。
- 涉及数据删除、协议破坏、权限扩大或不可逆操作时，先获得用户明确同意。

## 提交前检查

- [ ] `uv run pytest` 通过。
- [ ] 新增或修改的行为有测试。
- [ ] 相关 `docs/` 和 `README.md` 已更新。
- [ ] `config.example.json5` 与配置代码一致。
- [ ] 没有提交 `config.json5`、token 或其他密钥。
- [ ] 没有引入新的持久化、多群组、Webhook 或 `chat_id`。
- [ ] 协议字符串和字段名没有被无意修改。
- [ ] 日志中不会出现明文 token。

## 相关文档

- 术语：[术语表](01-glossary.md)
- 设计原因：[设计取舍](02-design-decisions.md)
- 用户文档索引：[docs/README.md](../docs/README.md)
