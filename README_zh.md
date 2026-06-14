# CLADA

**C**losed-**L**oop **A**utonomous **D**evelopment **A**rchitecture（闭环自主开发架构）

一个面向 AI 编程智能体的**运行时安全护栏（runtime safety harness）**。CLADA 用 `clada run` 包裹任意智能体进程（`claude`、`codex` 或你自己的命令）：在启动前将自身护栏文件设为只读、监控未授权写入、将每次会话记录为结构化 JSONL，并生成带有「按路径限定回滚方案」的审计报告 —— 让自主智能体无法悄悄改写约束自己的文件，同时每次运行都可审计、可回滚。

> **从这里开始 —— 当前已上线的核心能力：**
> ```bash
> pip install -e .
> clada run -- echo ok                   # 监督任意命令
> bash examples/protected_write_demo.sh  # 完整离线演示（无需 LLM，零 API 费用）
> ```
> 带注释的完整走查见 [`docs/demo.md`](docs/demo.md)。

> **当前状态速览。** 已实现并有测试覆盖（Phase 1–5）：`clada run` 会话监督器、
> 带 fail-closed 降级语义的策略引擎、脱敏后的 JSONL 事件日志、带安全「按路径限定回滚」
> 的审计报告。下文更宏大的治理蓝图 —— 交互式 Gateway 状态机、双检锁 Contract 生成、
> 进程挂起、容器隔离 —— 属于**目标设计，仍为计划中 / best-effort**，并非全部已上线，
> 每一项均有标注。详见实现路线图与 [`docs/known-limitations.md`](docs/known-limitations.md)。

## 运行时安全护栏（当前已上线）

这是 CLADA 中**已实现并由 `tests/` 覆盖**的部分。把任意智能体放到 `clada run`
下运行，每次会话都提供四项保证：

| 保证 | 实现方式 |
|------|----------|
| **护栏文件受保护** | CLADA 在智能体启动前，用 `chmod` 把自身的决策/运行时目录设为只读，并（在安装了 `fswatch` 时）加以监控；智能体无法改写约束自己的文件。*这是 best-effort 的「减速带」，不是沙箱 —— 见 [已知限制](docs/known-limitations.md)。* |
| **诚实的降级模式** | 当加固或监控不可用时，CLADA 记录结构化的「降级」状态并 **fail closed（退出码 78）**，而不是假装受到保护。 |
| **每次会话都有日志** | `runtime/sessions/` 下每行一个脱敏后的 JSONL 事件；密钥在写入前即被省略。Schema 见 [`docs/jsonl-event-schema.md`](docs/jsonl-event-schema.md)。 |
| **安全、限定范围的回滚** | `runtime/audits/` 下的审计报告区分「本次会话产生的改动」与「会话前已存在的改动」，回滚命令只针对本次会话动过的文件。 |

```bash
clada run -- claude        # 或：clada run -- codex，或任意命令
```

零成本离线试用（无需 LLM、无 API 费用）：

```bash
bash examples/protected_write_demo.sh
```

该演示运行一个 shell「智能体」，尝试一次受保护写入、展示其被拦截，并打印会话日志、
审计报告与回滚方案。完整走查：[`docs/demo.md`](docs/demo.md)；示例目录：[`examples/`](examples/)。

---

## 设计哲学

> 从这里往下描述的是 CLADA 正在演进的**更宏大的治理设计**。其中很大一部分（交互式
> Gateway、双检锁生成、进程挂起、容器隔离）仍为**计划中或 best-effort**，尚未上线 ——
> 每一项均在「核心特性」与「实现路线图」表中标注。上面的运行时护栏才是当前可用的部分。

AI 编程智能体能力强大但缺乏边界约束。它们会产生幻觉、偏离需求规格、并在数百轮迭代后抗拒回滚。CLADA 通过三项环环相扣的机制，对自主开发施加**宪法级约束**：

| 机制 | 角色 |
|------|------|
| **Contract**（contract.json） | 机器可读宪法 — 定义系统必须做什么、禁止做什么，以及如何验证 |
| **Gateway**（状态机） | 运行时控制器 — 强制执行 8 状态生命周期，卡控每一次状态转移 |
| **Verifier**（审计 + 双检锁） | 独立验证者 — 在合并前审计 Executor 的每一项输出 |

## 架构

```
┌─────────────────────────────────────────┐
│               Owner（项目负责人）          │
│             (斜杠命令 /slash)              │
└──────────┬──────────────────────────────┘
           │ /init, /propose, /execute, /merge, /abort
           ▼
┌─────────────────────────────────────────┐
│               Gateway（网关）              │
│  ┌───────────────────────────────────┐  │
│  │  状态机（8 个状态）                  │  │
│  │  PTY 管理器 · 模式监听器            │  │
│  │  心跳守护 · 文件访问代理             │  │
│  └───────────────────────────────────┘  │
└──────┬────────────────────┬─────────────┘
       │                    │
       ▼                    ▼
┌──────────────┐   ┌──────────────┐
│   Executor   │   │   Verifier   │
│  (执行者 AI)  │   │  (验证者 AI)  │
│              │   │              │
│  可写 src/   │   │  只读        │
│  禁止写      │   │  审计 +      │
│  docs/       │   │  仲裁        │
└──────────────┘   └──────────────┘
```

### 三权分立

| 权限 | Owner（人） | Executor（AI） | Verifier（AI） |
|------|-------------|----------------|----------------|
| 写源代码 | ✅ | ✅ | ❌ |
| 写文档 / Contract | ✅ | ❌（Gateway 拦截） | ✅ |
| 读源代码 | ✅ | ✅（只读） | ✅ |
| 触发状态转移 | ✅（斜杠命令） | 部分（输出触发词） | 部分（审计结论） |

### 状态机

```
IDLE ──/init──▶ BOOTSTRAP ──确认──▶ IDLE
  │
  ├──/propose──▶ PROPOSING ──spec就绪──▶ EXECUTING
  │                                          │  ▲
  │                     ┌────────────────────┤  │
  │                     │  [REQ_REVIEW]      │  │
  │                     ▼                    │  │
  │                 SUSPENDED ──裁决──▶ ARBITRATING
  │                                          │
  │                     [DONE]               │
  │                       ▼                  │
  │                   AUDITING ──失败──▶ EXECUTING
  │                       │
  │                  ┌────┴────┐
  │             通过+有B_PLAN  通过+无B_PLAN
  │                  │          │
  │                  ▼          ▼
  │         WAITING_FOR_OWNER  PENDING_COMMIT
  │                                │
  ◀──────────── /merge ───────────┘
```

## 核心特性

下表描述的是**目标设计**，“状态”列标明哪些已上线、哪些为 best-effort 或计划中（详见实现路线图）。

| 特性 | 说明 | 状态 |
|------|------|------|
| **ADR 决策记录** | 每一项架构决策以机器可读的 front-matter 格式记录，并经过形式化验证 | 已实现 |
| **三级记忆系统** | L1（即时上下文）、L2（结构化决策索引）、L3（历史存档）— 针对 100+ 轮迭代后的幻觉问题设计 | L2 索引已实现；L3 计划中 |
| **模式监听器** | 基于正则触发的状态转移（`[REQ_REVIEW]`、`[DONE]`、`[B_PLAN]`、`[TRACE]`） | best-effort（需实时 Gateway） |
| **物理隔离** | `SIGSTOP`/`SIGCONT` 挂起执行者；审计期间 `chmod 555` 将源码目录设为只读 | 计划中（Phase 2）—— 尚未强制启用 |
| **双检锁 Contract 生成** | 两个独立 AI 模型分别生成项目宪法；Gateway 逐字段比对；Owner 仅仲裁冲突点 | 计划中（Phase 3） |
| **Clean Shutdown 协议** | Quota 耗尽或异常终止时自动 git stash + 恢复选择提示 | 计划中（Phase 3） |

## 项目结构

```
CLADA/
├── src/
│   └── clada/                 # Python 包
│       ├── __init__.py        # 包导出
│       ├── __main__.py        # CLI 入口（python -m clada / clada）
│       ├── session.py         # clada run 会话监督器 + JSONL 日志（Phase 2）
│       ├── policy.py          # 路径策略 + 降级语义 + 脱敏（Phase 3）
│       ├── audit.py           # 会话审计报告生成（Phase 4）
│       ├── checkpoint.py      # Git 检查点 + 按路径限定回滚（Phase 4）
│       ├── orchestrator.py    # 状态机 + PTY 管理器 + FileAccessProxy
│       ├── bootstrap.py       # 引导流程 + 记忆管理器
│       ├── contract_validator.py  # Contract/DR 验证 + L2 索引
│       ├── config.py          # LLM 角色配置（.clada/config.yml）
│       └── dsl/               # S 表达式 DSL → contract.json + spec.md
├── examples/                  # 离线运行时护栏演示（无需 LLM）
│   ├── protected_write_demo.sh
│   └── agent_sim.sh
├── tests/                     # pytest 测试（监督器、策略、审计、检查点、验证器、DSL）
├── docs/
│   ├── demo.md                # 演示走查：输出、日志、审计、回滚
│   ├── jsonl-event-schema.md  # 会话 JSONL 事件 Schema + 示例
│   ├── known-limitations.md   # macOS chmod/fswatch 语义 + 推迟范围
│   └── CLADA_Complete_Spec.html  # 完整技术方案
├── pyproject.toml             # 打包配置 + 控制台脚本（clada）
├── requirements.txt           # pyproject.toml 依赖的扁平镜像
├── .gitignore
└── README.md
```

## 文档

| 文档 | 内容 |
|------|------|
| [`docs/demo.md`](docs/demo.md) | 端到端演示：预期输出、会话日志、审计报告、回滚路径。 |
| [`docs/jsonl-event-schema.md`](docs/jsonl-event-schema.md) | 每类会话 JSONL 事件的逐字段 Schema，含示例与退出码。 |
| [`docs/known-limitations.md`](docs/known-limitations.md) | `chmod`/`fswatch` 保护能与不能提供什么、fail-closed 行为、明确推迟的范围。 |
| [`examples/README.md`](examples/README.md) | 如何运行离线演示、如何换成真实智能体。 |

## 快速开始

### 安装（macOS / Linux，Python ≥ 3.9）

```bash
git clone https://github.com/Stanley-Zheong/CLADA.git
cd CLADA
python3 -m venv .venv && source .venv/bin/activate
pip install -e .                  # 安装 CLADA 及依赖，并注册 `clada` 命令
clada help                        # 验证安装
```

可选附加项：

```bash
pip install -e ".[test]"          # 基础安装的别名 —— pytest 已包含在基础依赖中
brew install fswatch              # 可选，用于文件写入监控（best-effort / 计划中）
npm install -g @anthropic-ai/claude-code  # Executor 智能体（仅运行实时 Gateway 时需要）
```

> `pip install -e .` 读取 `pyproject.toml`；`requirements.txt` 为同一依赖集合的
> 扁平镜像，供习惯 `pip install -r requirements.txt` 的环境使用。

### 运行测试

`pytest` 已包含在基础依赖中，因此执行 `pip install -e .` 后即可直接运行基线测试：

```bash
pytest                            # 运行 tests/ 下的基线测试
```

### 首次运行 —— 用 `clada run` 监督智能体

这是首选入口。`clada run -- <命令>` 把任意进程包进运行时安全护栏
（策略门禁 → 会话日志 → 审计报告 → 回滚）：

```bash
clada run -- echo ok              # 冒烟测试：监督一个简单命令
clada run -- claude               # 监督真实智能体（需安装 Claude Code CLI）
clada run -- codex                # ……或 PATH 上的任意命令
```

随后查看本次会话产生的内容：

```bash
ls runtime/sessions/              # <session-id>.jsonl —— 结构化事件日志
ls runtime/audits/                # <session-id>.md   —— 审计报告 + 回滚方案
```

想在无 LLM 的情况下端到端体验？运行离线演示：

```bash
bash examples/protected_write_demo.sh   # 走查见 docs/demo.md
```

> 若 `clada run` 退出码为 `78`，表示加固/监控降级、CLADA 已 fail closed
> （常见原因：未安装 `fswatch`）。安装 `fswatch` 后重试，或参见
> [`docs/known-limitations.md`](docs/known-limitations.md)。

### 其他命令（无需项目）

```bash
clada help                        # 列出所有命令
clada dsl domains                 # 列出内置 DSL 领域
```

### 引导新项目（计划中的治理流程）

> 交互式 Gateway 与 Bootstrap 引导流程为**计划中 / best-effort**，不属于当前已上线
> 的运行时护栏。详见实现路线图。

```bash
cd 你的项目目录
clada init                        # 引导：定义 Goal + Contract  （等价：python3 -m clada init）
clada                             # 启动 Gateway                （等价：python3 -m clada）
```

### Gateway 命令

```
clada> /init              启动引导流程（创建首个 Contract + DR-001）
clada> /propose [描述]     进入 PROPOSING：Verifier 细化 Spec
clada> /execute           启动 Executor 执行 current_spec.md
clada> /merge             合并 feature 分支（仅 PENDING_COMMIT 状态下可用）
clada> /reject [原因]      驳回审计结果，返回 EXECUTING
clada> /abort             安全关闭并退出
clada> /status            显示当前系统状态
clada> /quota [n]         设置 ask_verifier 配额（默认 10）
clada> /autopilot [on|off] 切换 Owner 离线模式
```

### CLI 命令

执行 `pip install -e .` 后，以下命令同时支持 `clada <cmd>` 与 `python3 -m clada <cmd>`。

```bash
clada run -- <命令>        # 监督一个命令（会话日志 + 审计 + 回滚）
clada status              # 查看系统状态
clada validate contract   # 验证 docs/spec/contract.json
clada validate dr <文件>   # 验证单个 DR-xxx.md
clada validate all        # 验证所有 DR
clada index rebuild       # 重建 L2 index.json
clada cold-start          # 扫描仓库生成 architecture.md
clada dsl domains         # 列出可用的 DSL 领域
clada dsl compile <文件>   # 编译 .dsl 文件 → contract.json + spec.md
clada dsl template <领域>  # 打印某领域的 DSL 模板
clada config init         # 创建默认 .clada/config.yml
```

> **成熟度说明。** 已实现并由 `tests/` 套件覆盖：`clada run` 会话监督器（Phase 2）、
> 带 fail-closed 降级语义与脱敏的策略引擎（Phase 3）、带安全「按路径限定回滚」的审计
> 报告（Phase 4），以及可安装打包、状态机、Contract/DR 验证器、DSL 编译器（Phase 1）。
> best-effort 或计划中：实时交互式 Gateway 循环（无参数的 `clada`）、PTY 挂起、容器隔离
> —— 详见实现路线图、技术风险登记与 [`docs/known-limitations.md`](docs/known-limitations.md)。
> `fswatch` 监控在安装了 `fswatch` 时生效；未安装时 `clada run` 会 fail closed，
> 而不是在无保护状态下运行。

## 实现路线图

项目按「先交付运行时安全护栏楔子，再叠加更宏大的治理 UI」的顺序推进。

| 阶段 | 范围 | 状态 |
|------|------|------|
| **Phase 1** | 可安装打包、状态机、Contract/DR 验证器、DSL 编译器、基线测试 | 已完成 |
| **Phase 2** | `clada run` 会话监督器 + 脱敏 JSONL 事件日志 | 已完成 |
| **Phase 3** | 策略引擎、诚实降级语义、fail-closed 门禁、脱敏 | 已完成 |
| **Phase 4** | 审计报告 + 安全的「按路径限定」会话回滚 | 已完成 |
| **Phase 5** | 可复现演示、与实现行为对齐的文档 | 已完成 |
| **后续** | 实时交互式 Gateway、双检锁 Bootstrap、PTY 挂起、容器隔离、L3 记忆 | 计划中 / best-effort |

## 技术风险登记

以下关键假设仍需实测验证：

- **RISK-01**：SIGSTOP 超过约 60 秒可能导致与 Anthropic API 的 TCP 连接超时 → 恢复时重新注入上下文
- **RISK-02**：fswatch 在 bind mount 场景下的捕获率 → 以 chmod 555 作为主防线
- **RISK-03**：心跳探针可能触发智能体意外响应 → PTY 层过滤
- **RISK-04**：macOS SIP 下 LD_PRELOAD 文件拦截不可用 → 以 chmod 权限管控作为替代方案

## License

MIT
