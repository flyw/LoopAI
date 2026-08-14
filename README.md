# LoopAI agent loop

一个由 Python `asyncio` 驱动的 spec-first 开发循环。调用方不指定具体 ticket；工具从
initiative 的 `spec.md` 出发，自动扫描同目录 `issues/*.md` 中的 ticket 元数据，创建并维护
`.loopai/execution.json`，再按照 blocker/frontier 顺序完成全部 ticket。initiative 的
`README.md` 只作为人类说明文档，不参与执行。

每个 initiative 使用一个 Coordinator，并为每个 ticket 使用两个独立 agent：

1. Coordinator 的启动 Prompt 第一行显式调用
   `$flyw:agent-initiative-orchestrator` skill。它检查 tracker、repository 和两个 agent 的
   最新状态，选择下一步受限动作；同一次运行内始终恢复同一个 Coordinator session。
2. Executor 的启动 Prompt 第一行显式调用 `$flyw:agent-ticket-executor` skill。
3. Verifier 的启动 Prompt 第一行显式调用 `$flyw:agent-ticket-verifier` skill 独立复验。
4. Verifier 返回 `incomplete` 时，反馈先交给 Coordinator，再送回同一个 Executor session，
   随后由同一个 Verifier
   session 复验。
   Executor 自己返回 `incomplete` 时也会自动恢复同一个 Executor session 重试，不会立即
   结束 initiative。
5. 只有当前 ticket 为 `completed` 才选择下一个依赖已解锁的 ticket；否则整个 initiative
   停止，绝不越过失败继续下游。

Coordinator 只能返回 schema 中声明的动作。Python 安全层会机械校验当前 ticket、依赖、
角色切换和 resume session ID；模型不能跳过 blocker、绕过独立验证或自行宣告完成。
程序重启后会重新读取 `.loopai/execution.json`，跳过 `completed`，并对
`ready-for-verification` 直接启动 Verifier，不会从第一张 ticket 重做。Coordinator session
ID 会保存在当前 initiative 的 `.loopai/sessions.json`。保存的 session 无法恢复时，会启动
新 session，并用持久化问答和当前 repository 状态重建上下文。

## Planner 决策、Grill 与外层 Agent 交接

Coordinator 是 LoopAI 的 Planner。它会优先检查 repository 和 tracker；缺少会改变范围、行为、
风险或验收方式的决策时返回 `ask-user`，需要外部验证证据或授权时返回 `await-user`。Grill 模式
仍由 `$mattpocock-skills:grilling` 驱动，但 LoopAI 不再在进程内显示输入框或等待终端输入。

任何无法安全继续的情况都会：

- 由 Planner 总结当前状态和阻塞原因；
- 在当前启动目录写入 `LOOPAI_STATUS.md`；
- 发出 `initiative.handoff` 事件并退出，退出码为 `1`。

外层 Agent 处理完状态文件中的事项后，在同一个项目目录中使用 `--answer` 恢复：

```bash
loopai \
  --spec .scratch/cropai-mvp/spec.md \
  --answer "已完成外部处理，请重新检查并继续"
```

`--answer` 是传给 Planner 的自由文本，可重复使用以提供脚本化的多轮结果。没有待恢复的
handoff 时传入 `--answer` 会报错。不要在回答中输入密码、API key 或凭据。

`LOOPAI_STATUS.md` 是外层 Agent 的快速入口，包含 initiative、当前 ticket、完成进度、Planner
总结、阻塞原因和下一次恢复命令。详细的持久化上下文仍在 initiative 的 `.loopai/` 目录中。

每个 initiative 的本机状态完全隔离：

```text
initiative/.loopai/
├── conversation.json
├── sessions.json
├── execution.json
└── active.lock
```

`execution.json` 由 LoopAI 自动创建和更新，保存 ticket 的状态、路径和依赖关系。不同 initiative
可以并行运行；同一 initiative 的第二个进程会被锁拒绝。`.loopai/` 和 `LOOPAI_STATUS.md` 会写入
当前目录的 `.git/info/exclude`，不会修改团队共享的 `.gitignore`。

三个角色的默认模型由当前目录的 `.loopai/config.toml` 管理。Coordinator、Executor 和
Verifier 默认都使用 `gpt-5.6-luna` / `medium`。唯一的模型
调用路径是本机
`codex exec --json` 子进程；本项目不使用 OpenAI SDK、不直接请求 Responses API，也不读取
API key。Codex 输出的每个 JSONL 事件都会通过异步迭代器立即转发。

Codex 的单条 JSONL 事件可能包含较大的命令输出。子进程读取上限默认为 64 MiB，避免
Python `asyncio` 默认 64 KiB 行限制中断长事件，同时保持逐事件流式转发。

## 预期目录结构

与 `../CropAI/.scratch/cropai-mvp` 一致：

```text
initiative/
├── spec.md
├── README.md              # 可选，仅供人类阅读
├── issues/
│   ├── 01-first.md        # 含 Status / Blocked by 元数据
│   └── 02-second.md
└── artifacts/
```

工具按 ticket 文件名中的数字 ID 和文件顺序建立稳定 frontier，并从 ticket 文件中的
`Blocked by` 构建依赖图。它会：

- 校验 ticket 文件、重复 ID、未知 blocker 和依赖环；
- 首次启动时自动创建 `.loopai/execution.json`，之后保留已有状态；
- 跳过 tracker 中已经 `completed` 的 ticket；
- 每完成一票后由 LoopAI 自动把状态写入 tracker，再重新读取 frontier；
- 如果 ticket 文件被删除，会停止并要求删除 tracker 后重新初始化，避免误丢失执行状态；
- 自动恢复 `ready-for-verification` 的票，只启动 Verifier；
- 当有多个 `spec.md` 时要求用 `--spec` 消除歧义。

## 前置条件

- Python 3.9+
- 已安装 `codex` CLI
- 已通过 `codex login` 完成本机 Codex 登录
- 已安装 `flyw:agent-initiative-orchestrator`、`flyw:agent-ticket-executor` 和
  `flyw:agent-ticket-verifier` skills
- 需要 Grill 模式时已安装 `mattpocock-skills:grilling` skill
- 当前启动目录是 Git repository（三个 skills 需要读取真实 repository 状态）

## 安装与运行

LoopAI 需要 Python 3.9+、Codex CLI 和已登录的本机 Codex 环境。先完成：

```bash
codex login
```

以下安装方式任选其一。

### 方式一：使用 uv 安装 CLI（推荐）

`uv` 会创建独立环境，并根据 `pyproject.toml` 安装依赖；Python 3.9/3.10 会自动安装
`tomli`。

如果尚未安装 uv（macOS + Homebrew）：

```bash
brew install uv
```

安装 LoopAI：

```bash
cd /path/to/LoopAI
uv tool install --editable .
```

代码或依赖更新后，强制刷新已有工具环境：

```bash
uv tool install --force --editable /path/to/LoopAI
```

验证：

```bash
loopai --help
```

### 方式二：使用 Python 虚拟环境和 pip

```bash
cd /path/to/LoopAI
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/loopai --help
```

如果希望在任意目录直接使用该虚拟环境中的命令，可以把它加入当前 shell 的 `PATH`：

```bash
export PATH="/path/to/LoopAI/.venv/bin:$PATH"
cd /path/to/your/project
loopai
```

### 方式三：构建 macOS 单文件版本

这种方式不安装 LoopAI Python 包，生成的可执行文件可以复制到其他目录使用：

```bash
cd /path/to/LoopAI
python3 -m pip install pyinstaller
sh scripts/build-macos.sh
```

构建产物为 `dist/loopai`。当前 Mac 为 Apple Silicon 时产物是 `arm64`；Intel Mac 需要在
Intel Mac 上重新构建。

从项目目录运行：

```bash
cd /path/to/your/project
/path/to/LoopAI/dist/loopai
```

也可以把构建产物目录加入 `PATH`，直接输入 `loopai`：

```bash
export PATH="/path/to/LoopAI/dist:$PATH"
cd /path/to/your/project
loopai
```

单文件版本不包含 Codex CLI、登录状态或项目内容；运行机器仍需安装 Codex CLI 并
执行 `codex login`。

### 选择 spec

当当前目录下只有一个 `spec.md` 时，可以省略 `--spec`。有多个 spec 时，先查看路径：

```bash
rg --files .scratch | rg '/spec\.md$'
```

然后指定要执行的 initiative：

```bash
loopai \
  --spec .scratch/recording-dataset-management/spec.md
```

相对 `--spec` 路径会以当前启动目录为基准解析，并且 spec 必须位于该目录内。

### 三个 Agent 的模型配置

第一次在当前目录启动 `loopai` 时，会自动创建：

```text
<current-directory>/.loopai/config.toml
```

默认内容：

```toml
[coordinator]
model = "gpt-5.6-luna"
reasoning_effort = "medium"
startup_prompt = """请使用中文与用户交互。"""

[executor]
model = "gpt-5.6-luna"
reasoning_effort = "medium"

[verifier]
model = "gpt-5.6-luna"
reasoning_effort = "medium"
```

首次创建后会继续运行；编辑该文件后，下次启动生效。配置严格校验，未知 section/key、
空 model、非法 TOML 或不支持的 reasoning effort 会在启动 Agent 前报错。

Coordinator 还可以配置通用启动提示，例如：

```toml
[coordinator]
model = "gpt-5.6-luna"
reasoning_effort = "medium"
startup_prompt = """
请使用中文与用户交互。
提问时尽量简洁，并给出推荐答案。
"""
```

`startup_prompt` 会注入每一次 Coordinator prompt，包括有效 session 的恢复调用；Executor 和
Verifier 不会收到它。你可以把语言、提问方式和本项目的其他要求统一写在这里。

临时覆盖全部角色：

```bash
loopai --model gpt-5.6-luna --reasoning-effort medium
```

只覆盖某个角色：

```bash
loopai \
  --coordinator-model gpt-5.6-luna \
  --coordinator-reasoning-effort max \
  --executor-model gpt-5.6-luna \
  --verifier-model gpt-5.6-luna
```

配置优先级：

```text
角色 CLI 参数 > 全局 CLI 参数 > 当前目录 TOML > 内置角色默认值
```

当当前目录内只有一个 `spec.md` 时，无需提供 spec 或 ticket：

```bash
cd ../CropAI
loopai
```

有多个 spec 时只选择 initiative，而不是选择 ticket：

```bash
loopai \
  --spec .scratch/cropai-mvp/spec.md
```

默认输出为精简的人类可读进度。需要供 SSE、WebSocket、日志处理器或消息总线消费的完整
JSONL 时，使用 `--json`：

```bash
loopai --json > loopai-events.jsonl
```

实际启动的新 Agent 命令等价于：

```bash
codex exec \
  --json \
  --model gpt-5.6-luna \
  -c 'model_reasoning_effort="medium"' \
  --approve-for-me \
  --cd . \
  -
```

后续轮次通过 `codex exec resume <session-id> -` 恢复同一个 Executor 或 Verifier。Prompt
始终经标准输入传递，不经过 shell 拼接。

## Python 响应式接口

```python
import asyncio
from pathlib import Path

from loopai import InitiativeOrchestrator, LoopConfig


async def main() -> None:
    config = LoopConfig(
        working_directory=Path("../CropAI"),
        model="gpt-5.6-luna",
        reasoning_effort="medium",
        max_rounds=3,
    )
    orchestrator = InitiativeOrchestrator(config)
    async for event in orchestrator.stream(
        Path(".scratch/cropai-mvp/spec.md")
    ):
        print(event.as_dict())


asyncio.run(main())
```

主要事件：

- `initiative.started`
- `ticket.started`
- `agent.event`：Codex 原始 JSONL
- `agent.stderr`
- `agent.completed`
- `ticket.completed`
- `initiative.completed`
- `initiative.handoff`

只有整个 initiative 的所有 ticket 都完成时 CLI 返回 `0`。等待外层 Agent、阻塞、失败或达到
最大轮次时发出 `initiative.handoff` 并返回 `1`；初始化、tracker 或配置错误返回 `2`。

## 示例与测试

[examples/spec.md](examples/spec.md)、[examples/README.md](examples/README.md) 和
`examples/issues/*.md` 展示了两张具有依赖关系的 ticket。示例需求是占位内容，不建议直接
交给 agent 修改本仓库。

测试不会连接模型：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python3 -m unittest discover -s tests -v
```
