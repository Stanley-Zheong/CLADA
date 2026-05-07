# CLADA

**C**losed-**L**oop **A**utonomous **D**evelopment **A**rchitecture（闭环自主开发架构）

一个将 AI 编程智能体（如 Claude Code）置于可验证开发流水线中的治理框架 — 通过机器可读宪法、形式化状态机和物理隔离，让人始终掌控全局。

## 设计哲学

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

- **双检锁 Contract 生成**：两个独立 AI 模型分别生成项目宪法；Gateway 逐字段比对；Owner 仅仲裁冲突点。杜绝单一模型偏见写入宪法。
- **物理隔离**：`SIGSTOP`/`SIGCONT` 挂起执行者；审计期间 `chmod 555` 将源码目录设为只读。
- **模式监听器**：基于正则触发的状态转移（`[REQ_REVIEW]`、`[DONE]`、`[B_PLAN]`、`[TRACE]`）。
- **三级记忆系统**：L1（即时上下文）、L2（结构化决策索引）、L3（历史存档）— 针对 100+ 轮迭代后的幻觉问题设计。
- **Clean Shutdown 协议**：Quota 耗尽或异常终止时自动 git stash + 恢复选择提示。
- **ADR 决策记录**：每一项架构决策以机器可读的 front-matter 格式记录，并经过形式化验证。

## 项目结构

```
CLADA/
├── src/
│   └── clada/                 # Python 包
│       ├── __init__.py        # 包导出
│       ├── __main__.py        # CLI 入口（python -m clada）
│       ├── orchestrator.py    # 状态机 + PTY 管理器 + Gateway REPL
│       ├── bootstrap.py       # 引导流程 + 记忆管理器
│       └── contract_validator.py  # Contract/DR 验证 + L2 索引
├── docs/
│   └── CLADA_Complete_Spec.html  # 完整技术方案
├── requirements.txt
├── .gitignore
└── README.md
```

## 快速开始

### 前置依赖（macOS）

```bash
pip install rich psutil jsonschema pexpect watchdog
brew install fswatch              # 可选，用于文件写入监控
npm install -g @anthropic-ai/claude-code  # Executor 智能体
```

### 引导新项目

```bash
cd 你的项目目录
python3 -m clada init             # 引导：定义 Goal + Contract
python3 -m clada                  # 启动 Gateway
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

```bash
python3 -m clada status             # 查看系统状态
python3 -m clada validate contract  # 验证 docs/spec/contract.json
python3 -m clada validate dr <文件>  # 验证单个 DR-xxx.md
python3 -m clada validate all       # 验证所有 DR
python3 -m clada index rebuild      # 重建 L2 index.json
python3 -m clada cold-start         # 扫描仓库生成 architecture.md
```

## 实现路线图

| 阶段 | 范围 | 状态 |
|------|------|------|
| **Phase 1** | PTY 封装、状态机、Contract 验证器、Bootstrap 引导 | 开发中 |
| **Phase 2** | Docker 测试隔离、chmod 锁、fswatch、心跳守护、L2 索引 | 计划中 |
| **Phase 3** | 双检锁 Bootstrap UI、L3 向量库、Clean Shutdown 协议、Owner 控制台 | 计划中 |

## 技术风险登记

以下关键假设仍需实测验证：

- **RISK-01**：SIGSTOP 超过约 60 秒可能导致与 Anthropic API 的 TCP 连接超时 → 恢复时重新注入上下文
- **RISK-02**：fswatch 在 bind mount 场景下的捕获率 → 以 chmod 555 作为主防线
- **RISK-03**：心跳探针可能触发智能体意外响应 → PTY 层过滤
- **RISK-04**：macOS SIP 下 LD_PRELOAD 文件拦截不可用 → 以 chmod 权限管控作为替代方案

## License

MIT
