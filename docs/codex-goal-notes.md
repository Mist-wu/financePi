# Codex `/goal` 机制研究笔记

参考仓库：https://github.com/openai/codex

结论：Codex `/goal` 不是简单的 slash prompt，而是一套 **持久目标 + 自动续跑 + 运行时注入上下文 + 状态管理** 的机制。

## 1. 相关源码位置

```text
codex-rs/tui/src/chatwidget/slash_dispatch.rs
codex-rs/tui/src/app/thread_goal_actions.rs
codex-rs/core/src/goals.rs
codex-rs/core/templates/goals/continuation.md
codex-rs/core/templates/goals/budget_limit.md
codex-rs/core/templates/goals/objective_updated.md
codex-rs/ext/goal/src/spec.rs
codex-rs/ext/goal/src/tool.rs
codex-rs/protocol/src/protocol.rs
```

## 2. 用户侧用法

`/goal` 是实验功能，需要开启：

```toml
[features]
goals = true
```

常见命令：

```text
/goal <objective>   设置长期目标
/goal               查看当前目标
/goal pause         暂停目标
/goal resume        恢复目标
/goal clear         清除目标
/goal edit          编辑目标
```

目标文本限制：

```text
objective 非空
最多 4000 字符
更长说明建议写入文件，再在 goal 中引用该文件
```

## 3. Goal 被持久化到 thread 状态

Codex 不只是把目标塞进当前 prompt，而是把目标存在 thread 状态库中。

结构大致类似：

```json
{
  "thread_id": "...",
  "objective": "...",
  "status": "active",
  "token_budget": 10000,
  "tokens_used": 1234,
  "time_used_seconds": 300,
  "created_at": "...",
  "updated_at": "..."
}
```

状态包括：

```text
active
paused
blocked
usageLimited
budgetLimited
complete
```

## 4. 空闲时自动继续

核心逻辑在：

```text
maybe_start_goal_continuation_turn()
```

Codex 会检查：

```text
- goals 功能是否开启
- 当前是否是 Plan mode
- 是否已有正在运行的 turn
- 是否有用户输入排队
- 是否有 trigger-turn mailbox 输入
- 当前 thread 是否有 active goal
```

如果满足条件，Codex 会自动启动一个新的 turn，让模型继续朝目标工作。

简化逻辑：

```text
目标仍然 active
  ↓
当前 thread 空闲
  ↓
系统构造隐藏 user message
  ↓
模型继续工作
```

## 5. 通过隐藏 `<goal_context>` 提醒模型

Codex 会注入隐藏上下文，格式类似：

```text
<goal_context>
Continue working toward the active thread goal.

<objective>
用户设置的长期目标
</objective>

Budget:
- Tokens used: ...
- Token budget: ...
- Tokens remaining: ...

Work from evidence:
Use the current worktree and external state as authoritative...
</goal_context>
```

重点：

```text
目标 objective 被当成用户提供的数据，而不是更高优先级系统指令。
```

这样可以降低 prompt injection 风险。

## 6. 模型可用的 goal 工具

Codex 给模型提供这些工具：

```text
get_goal
create_goal
update_goal
```

其中 `update_goal` 很克制，只允许模型把目标标记为：

```text
complete
blocked
```

模型不能自己随便设置：

```text
pause
resume
budgetLimited
usageLimited
```

这些状态由用户或系统控制。

## 7. Completion audit

Codex 的 continuation prompt 明确要求：

```text
不要因为做了一点进展就标记 complete。
必须逐条验证目标要求。
必须用当前文件、命令输出、测试结果、外部状态等权威证据证明完成。
证据不足时保持 active，继续推进。
```

核心思想：

```text
完成是一个需要证据证明的声明，不是模型感觉已经差不多。
```

## 8. Blocked audit

Codex 不允许模型第一次遇到阻塞就标记 blocked。

规则大致是：

```text
同一个阻塞条件至少连续出现 3 个 goal turn，才允许 update_goal(status="blocked")。
```

并且 blocked 只能用于：

```text
确实无法继续推进，需要用户输入或外部状态改变。
```

不能因为任务困难、缓慢、不确定就标记 blocked。

## 9. Budget limit

当目标达到 token budget，系统会标记为 `budgetLimited`，并注入 `budget_limit.md` 提示。

模型被要求：

```text
不要开始新的实质工作。
尽快收尾。
总结进展、剩余工作和下一步。
除非目标真的完成，否则不要调用 update_goal。
```

## 10. 对本项目交易 Agent 的启发

我们要模仿的不是固定交易流程，而是 Codex 的长期目标机制：

```text
持久目标
  ↓
定时 / 新闻 / 市场异动触发
  ↓
注入 goal_context
  ↓
AI 自主探索
  ↓
如果要交易，必须走受限交易协议
```

适合本项目的 goal 示例：

```text
/goal
持续盯盘 Binance USD-M 合约市场，结合行情、新闻、宏观、链上数据和市场情绪，寻找中短线非高频交易机会。
优先保护本金。所有实盘交易必须有 thesis、止损、失效条件和复盘记录。
任何真实资金操作必须通过 create_trade_proposal、risk_check 和 execute_approved_trade。
禁止绕过风控、无止损开仓、亏损摊平加仓、超杠杆交易或因单条新闻重仓。
```

## 11. 本项目应该借鉴的实现点

### 11.1 持久 goal 表

建议在 SQLite 中保存：

```text
goals
- goal_id
- objective
- status
- risk_mode
- created_at
- updated_at
- last_wakeup_at
- tokens_used / calls_used，可选
```

### 11.2 Supervisor 自动唤醒

Codex 主要是在 thread 空闲时自动继续；交易系统还需要 Python Supervisor 定时或事件驱动唤醒：

```text
- 每 N 分钟唤醒
- 新闻突发时唤醒
- 市场异动时唤醒
- 持仓接近止损/止盈时唤醒
```

### 11.3 Trading goal context

每次唤醒时注入类似：

```text
<trading_goal_context>
Active goal:
...

Account state:
...

Risk status:
...

Market anomalies:
...

Recent news:
...

You may freely explore. If and only if you want to trade real funds, submit a structured trade proposal.
</trading_goal_context>
```

### 11.4 固定的是执行协议，不是研究流程

```text
AI 可以自由探索；
但真实资金执行必须固定协议：

create_trade_proposal
  ↓
risk_check
  ↓
approval_id
  ↓
execute_approved_trade
```

这也是 Codex `/goal` 给本项目最大的启发：

```text
让目标长期持续，让模型自主推进；
但把关键状态变更和危险动作交给程序化工具约束。
```
