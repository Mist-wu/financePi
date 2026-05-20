# Pi 自主交易 Agent 设计草案

目标：Pi 可以主导探索、分析和交易决策，但不能直接裸连 Binance；实盘执行必须通过程序化限制和风控闸门。

核心原则：**AI 自由探索，执行严格受限**。

## 1. Binance API 权限隔离

建议在 Binance 创建两个 API Key：

### 1.1 只读 API Key

用途：交给 Pi Agent 使用。

权限：

```text
- 读取行情
- 读取账户信息
- 读取持仓
- 读取订单
- 读取成交记录
- 禁止交易
- 禁止提现
```

Pi 可以通过只读工具自由探索市场、持仓、新闻和情绪，但不能直接下单。

### 1.2 交易 API Key

用途：只交给本地交易执行服务，不直接暴露给 Pi。

权限：

```text
- 允许 USD-M 合约交易
- 禁止提现
- 尽量绑定固定 IP
- 只在执行服务进程中读取
```

交易 API 必须被封装成受限执行工具，限制内容包括：

```text
- 可交易合约白名单
- 最大杠杆
- 单笔最大风险
- 单币最大仓位
- 总仓位上限
- 强制止损
- 熔断规则
- 冷却期
- 审批过期时间
```

## 2. Pi 能调用哪些工具

### 2.1 工具分层

```text
Pi / AI 决策层
  ↓
只读工具 + 提案工具 + 受限执行入口
  ↓
风控 / 执行服务
  ↓
Binance 交易 API
```

禁止提供原始万能接口，例如：

```text
binance_raw_api(method, path, params)
```

### 2.2 推荐工具清单

#### A. 行情工具，只读

```text
get_market_snapshot(symbol)
get_klines(symbol, interval, limit)
get_funding_rate(symbol)
get_orderbook(symbol, depth)
get_open_interest(symbol)
scan_market_candidates(limit)
```

#### B. 新闻、宏观与情绪工具，只读

```text
get_news_context(asset, lookback_hours)
search_crypto_news(query, lookback_hours)
get_macro_context()
get_defi_context(asset_or_chain)
get_market_sentiment(asset_or_topic)
watch_news_feed(topics)
```

#### C. 量化分析工具，只读/计算型

```text
calculate_signal(symbol, timeframe)
calculate_volatility(symbol, timeframe)
calculate_liquidity_score(symbol)
backtest_simple_strategy(symbol, strategy_id, lookback_days)
rank_opportunities(candidates)
```

#### D. 账户工具，只读

```text
get_account_state()
get_positions()
get_open_orders(symbol?)
get_recent_trades(limit)
get_daily_pnl()
```

#### E. 记忆、日志与复盘工具

```text
get_state_summary()
record_observation(observation)
record_decision(decision)
record_review(review)
search_memory(query)
```

#### F. 交易提案与风控工具

```text
create_trade_proposal(proposal)
risk_check(proposal_id)
get_risk_limits()
get_risk_status()
cancel_approval(approval_id)
```

#### G. 受限执行工具

```text
execute_approved_trade(approval_id)
cancel_order(order_id, symbol)
close_position_with_risk_check(symbol, reason)
```

规则：

- Pi 不能直接传 Binance 下单参数。
- Pi 只能提交交易提案。
- `risk_check` 通过后生成 `approval_id`。
- `execute_approved_trade` 只能执行审批后的固定订单。
- 审批必须短期有效，例如 60 秒。
- 执行服务需要二次校验，防止审批后市场状态突变。

## 3. AI 决策模式：Goal-driven 自主探索

你不想把 AI 锁死在固定流程里，这个方向合理。这里建议模仿 Codex `/goal` 的思路：给 Agent 一个长期目标，让它持续观察、研究、形成假设、验证假设、交易和复盘。

关键不是规定“每次必须按 1-2-3-4 做”，而是规定：

```text
AI 可以自由决定研究路径；
但凡要动用真实资金，必须经过固定的提案与风控协议。
```

### 3.1 长期目标示例

```text
/goal
你是 CryptoPilot，一个长期运行的加密货币交易 Agent。

目标：
- 持续盯盘 Binance USD-M 合约市场。
- 主动探索新闻、宏观、链上数据、市场情绪和价格结构。
- 寻找中短线非高频交易机会。
- 优先保护本金，其次追求稳定收益。
- 所有实盘交易必须有 thesis、止损、失效条件和复盘记录。

允许：
- 自主决定观察哪些币种。
- 自主决定查询哪些新闻和数据。
- 自主建立、更新、废弃交易假设。
- 自主选择继续观察、提交交易提案、减仓、平仓或复盘。

禁止：
- 绕过风控。
- 无止损开仓。
- 亏损摊平加仓。
- 使用超出系统限制的杠杆。
- 因单条新闻重仓。
- 在熔断、冷却期或风控拒绝后强行交易。
```

### 3.2 自主循环，不是固定交易流程

系统可以用 Supervisor 定时唤醒 Agent，但不强制固定分析路径：

```text
每 N 分钟或有重大事件时：
  1. 把状态摘要、新闻摘要、市场异动推给 Pi。
  2. Pi 自主决定下一步：继续研究 / 记录观察 / 提交提案 / 放弃机会 / 复盘。
  3. 若 Pi 提交交易提案，程序强制进入 risk_check。
  4. 只有审批通过，执行服务才允许下单。
```

### 3.3 AI 输出可以宽松，交易提案必须严格

普通观察可以是自由文本：

```text
BTC 资金费率升高但价格未突破，可能是多头拥挤。继续观察 ETH、SOL 是否跟随。
```

但交易提案必须是结构化 JSON：

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
  "reason": "ETH 站上 EMA20，资金费率中性，新闻面无明显利空。",
  "invalid_if": "跌破 2090 或 BTC 跌破关键支撑"
}
```

## 4. 风控工具如何拦截危险操作

### 4.1 硬规则

建议初始默认值：

```text
交易合约白名单：BTCUSDT、ETHUSDT、SOLUSDT 起步
单笔最大风险：账户权益 0.5%
单币最大名义仓位：账户权益 5%
总名义仓位：账户权益 15%
最大杠杆：2x
每日最大亏损：账户权益 2%
连续亏损暂停：3 笔
审批有效期：60 秒
强制止损：必须存在
风险回报比：至少 1:1.5，低于则拒绝或降级
```

### 4.2 `risk_check` 检查项

```text
1. Schema 校验
   - 字段完整、类型正确、symbol 合法、action 合法

2. 合约白名单校验
   - 只允许交易配置文件中的 symbol

3. 止损校验
   - 开仓必须有 stop_loss
   - stop_loss 方向必须正确
   - 根据止损计算最大亏损

4. 仓位校验
   - 单笔 notional 不超过限制
   - 单币总风险不超过限制
   - 账户总风险不超过限制

5. 杠杆校验
   - leverage <= max_leverage

6. PnL 熔断
   - 今日亏损超过阈值则拒绝
   - 连续亏损超过阈值则拒绝

7. 冷却期
   - 同一 symbol 刚止损后进入冷却
   - 风控拒绝后禁止立即重复提交相似提案

8. 持仓冲突
   - 已有反向仓位时禁止直接开相反方向，除非是明确 close/reduce
   - 禁止亏损摊平加仓

9. 流动性校验
   - 深度不足、点差过大、滑点过高则拒绝

10. 事件风险
   - 重大宏观事件、交易所维护、极端波动时拒绝或降额
```

### 4.3 风控返回格式

通过：

```json
{
  "approved": true,
  "approval_id": "trade_abc123",
  "expires_at": "2026-05-21T12:01:00Z",
  "approved_order": {
    "symbol": "ETHUSDT",
    "side": "BUY",
    "type": "MARKET",
    "notional_usdt": 100,
    "leverage": 2,
    "stop_loss": 2090,
    "take_profit": 2180
  }
}
```

拒绝：

```json
{
  "approved": false,
  "reasons": [
    "symbol_not_whitelisted",
    "missing_stop_loss",
    "daily_loss_limit_reached"
  ],
  "suggested_fix": "降低仓位、补充止损，或等待风险状态恢复。"
}
```

### 4.4 执行侧二次拦截

`execute_approved_trade(approval_id)` 仍需二次检查：

```text
- approval_id 存在且未过期
- approved_order 未被篡改
- 当前价格偏离审批时价格不超过阈值
- 当前账户状态未触发新熔断
- 下单成功后立即创建/确认止损单
- 所有执行结果写入数据库
```

## 5. 推荐落地架构

```text
Python Supervisor
  ├─ 定时收集行情 / 新闻 / 情绪
  ├─ 维护 SQLite 状态和记忆
  ├─ 将状态摘要推给 Pi goal agent
  ├─ 接收 Pi 的观察、研究请求和交易提案
  ├─ 调用 risk_check
  └─ 调用受限执行服务

Pi Agent
  ├─ 自主探索
  ├─ 研究新闻和市场情绪
  ├─ 形成交易假设
  ├─ 提交交易提案
  └─ 复盘

Execution Service
  ├─ 持有 Binance 交易 API Key
  ├─ 执行硬风控
  ├─ 只执行 approval_id
  └─ 记录订单和成交
```

## 6. 下一步落地顺序

1. 配置双 Binance API Key：只读 Key + 交易 Key。
2. 定义工具接口和交易提案 schema。
3. 建 SQLite 表：observations、proposals、risk_checks、orders、positions、reviews。
4. 先实现只读数据工具、新闻工具和记忆工具。
5. 实现 `/goal` 风格 Supervisor 循环。
6. 实现 `risk_check`，用危险案例测试。
7. 先 paper trading，再小资金实盘。
