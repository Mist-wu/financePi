# AI阅读的本项目指南

## 设计

“AI 主导 + 工具约束 + 风控强制闸门”，不要让 AI 拿到无限制 Binance 权限。

────────────────────────────────────────────────────────────────────────────────

我会这样重新定义架构

```text
                      你
                      │
                      ▼
           ┌────────────────────┐
           │  Pi 长期运行 Agent  │
           │  分析 / 决策 / 复盘  │
           └─────────┬──────────┘
                     │ 调用工具
                     ▼
   ┌─────────────────────────────────────────┐
   │              工具层 / API 层             │
   ├─────────────────────────────────────────┤
   │ 新闻工具：RSS / NewsAPI / Tavily / DDGS  │
   │ 行情工具：Binance K线 / 资金费率 / 深度     │
   │ 量化工具：趋势 / 波动率 / 筛币 / 回测       │
   │ 账户工具：持仓 / 订单 / 余额 / 盈亏         │
   │ 风控工具：仓位 / 杠杆 / 止损 / 熔断       │
   │ 执行工具：下单 / 撤单 / 止盈止损          │
   └─────────────────────────────────────────┘
                     │
                     ▼
                 Binance
```

关键是：Pi 可以决定“想交易什么”，但执行工具必须被设计成受限的。

────────────────────────────────────────────────────────────────────────────────

AI 可以是主驾驶，但不能直接裸连 Binance

不要给 Pi 一个这样的工具：

```text
binance_raw_api(method, path, params)
```

因为这等于让 AI 随便调 Binance。

应该给它的是这些受控工具：

```text
get_market_snapshot(symbol)
scan_opportunities()
get_news_context(asset)
get_account_state()
calculate_signal(symbol)
propose_trade(...)
risk_check(proposal)
execute_approved_trade(approval_id)
review_recent_trades()
```

也就是说，AI 的操作流程应该是：

```text
发现机会
  ↓
收集新闻 + 行情 + 资金费率 + 技术指标
  ↓
生成交易提案
  ↓
调用风控检查
  ↓
风控通过后才能执行
  ↓
记录日志
  ↓
后续复盘
```

────────────────────────────────────────────────────────────────────────────────

推荐的交易提案格式

AI 每次想交易，必须先输出结构化提案：

```json
{
  "symbol": "ETHUSDT",
  "action": "open_long",
  "timeframe": "15m",
  "entry_type": "market",
  "notional_usdt": 100,
  "leverage": 2,
  "stop_loss": 2090,
  "take_profit": 2180,
  "confidence": 0.72,
  "reason": "ETH 资金费率回落，价格站上 EMA20，新闻面无明显利空。",
  "invalid_if": "跌破 2090 或 BTC 同步跌破关键支撑"
}
```

然后程序风控检查：

```text
是否超过最大仓位？
是否超过最大杠杆？
是否有止损？
是否在冷却期？
今日亏损是否超限？
是否和现有仓位冲突？
是否流动性足够？
```

只有风控返回：

```json
{
  "approved": true,
  "approval_id": "trade_abc123"
}
```

AI 才能调用：

```text
execute_approved_trade("trade_abc123")
```

这就是 两阶段下单。

────────────────────────────────────────────────────────────────────────────────

长期运行状态怎么做

Pi 自己的 session 可以保存对话，但不适合当交易系统数据库。

长期状态应该存在外部：

```text
SQLite / DuckDB / JSONL
```

保存：

```text
新闻原文
AI 提取的事件因子
行情快照
量化指标
交易提案
风控结果
真实订单
成交结果
复盘结论
每日总结
```

Pi 每隔一段时间读取“当前状态摘要”，而不是把所有历史都塞进上下文。

────────────────────────────────────────────────────────────────────────────────

长期运行方式有两种

### 方案 A：Pi Extension 做长期 Agent

Pi 扩展里加定时器：

```text
每 5 分钟：
  scan market
  scan news
  如果发现机会，自动给 Pi 发消息
```

优点：

- Pi 原生体验好
- Agent 感强

缺点：

- Pi 本质还是 coding harness，不是专门的交易 daemon
- 长期稳定性、重启恢复、异常处理要额外设计

────────────────────────────────────────────────────────────────────────────────

### 方案 B：Python Supervisor + Pi RPC

这个我更推荐。

```text
Python 长期进程负责：
  定时任务
  数据采集
  状态保存
  风控
  调用 Pi RPC 让 AI 分析和决策
```

Pi 仍然是“大脑”，但 Python 是“身体和神经系统”。

```text
Python daemon
   │
   ├── 收集行情/新闻
   ├── 筛出候选币
   ├── 调用 pi --mode rpc
   ├── 让 AI 分析
   ├── 执行风控
   └── 下单/复盘
```

这仍然是 基于 Pi，只是长期运行和工程可靠性由 Python 承担。

────────────────────────────────────────────────────────────────────────────────

AI 自己寻找机会的合理方式

不建议让 AI 从几百个币里盲看。

应该先用量化程序预筛：

```text
成交量异常
价格突破
资金费率极端
OI 异动
大跌反弹
多空爆仓
链上 TVL/费用变化
新闻热度异常
```

Python 工具返回 Top N：

```json
[
  {
    "symbol": "SOLUSDT",
    "reason": "24h volume +180%, price above EMA20/60, funding neutral",
    "score": 0.81
  },
  {
    "symbol": "ETHUSDT",
    "reason": "news momentum positive, breakout pending",
    "score": 0.74
  }
]
```

然后 Pi 对这些候选做深入研究。

────────────────────────────────────────────────────────────────────────────────

你给 AI 的“指导”应该是什么

你需要给 Pi 一个类似“交易宪法”的东西：

```text
你是 CryptoPilot 交易 Agent。

目标：
- 发现中短线加密货币交易机会
- 优先保护本金
- 不追求高频
- 所有交易必须有明确 thesis、止损、失效条件

禁止：
- 无止损开仓
- 加仓摊平亏损
- 在风控拒绝后绕过执行
- 因单条新闻直接重仓
- 使用超过指定杠杆

偏好：
- 趋势 + 新闻 + 资金费率共振
- 明确风险回报比
- 避免流动性差的小币
```

这个指导可以放在 Pi 的系统提示、Skill、或者项目 AGENTS.md 里。

────────────────────────────────────────────────────────────────────────────────

最重要的风险边界

如果“主要由 AI 操作”，我建议至少保留这些硬规则：

```text
单笔最大亏损：账户权益 0.5% - 1%
单币最大仓位：账户权益 5% - 10%
最大杠杆：2x 或 3x 起步
每日最大亏损：2% 后停止交易
连续亏损 N 笔后暂停
所有开仓必须带 stop loss
所有 live 下单必须记录日志
默认小资金实盘
```

这样 AI 可以主导，但不会一次犯错毁掉账户。

────────────────────────────────────────────────────────────────────────────────

我觉得你的目标可以这样表述

不是：

│ AI 辅助量化程序交易

而是：

│ Pi 驱动的自主交易 Agent，外接新闻、行情、量化和执行工具，由程序风控约束其交易行为。

这个定位是成立的。

我建议下一步先设计三件事：

1. Pi 能调用哪些工具
2. AI 交易决策的固定流程
3. 风控工具如何拦截 AI 的危险操作

先把这三件事定下来，再写代码会比较稳。

## 构建

### Docs

- DefiLlama API docs/api-sources.md
- duckduckgo_search https://raw.githubusercontent.com/deedy5/ddgs/refs/heads/main/README.md
- Binance https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info
- Fred https://raw.githubusercontent.com/mortada/fredapi/refs/heads/master/README.md
- Tavily AI docs https://docs.tavily.com/llms.txt
- Newsapi https://newsapi.org/docs

### RSS

#### 币圈实时新闻

- CoinDesk RSS: https://www.coindesk.com/arc/outboundfeeds/rss/
- CoinTelegraph RSS: https://cointelegraph.com/rss

#### 权威宏观与全球时事

- 华尔街日报 (WSJ) 核心新闻: https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml
- FT 中文网: http://www.ftchinese.com/rss/feed

## 测试

## 日志与复盘

学习之前市场。
