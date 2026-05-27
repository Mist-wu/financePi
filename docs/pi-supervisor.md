# Pi 集成盯盘 Supervisor

## 目标

把现有“固定规则脚本”升级为：

```text
Python supervisor 负责采集 / 风控 / 执行
Pi RPC 负责阅读快照 / 新闻 / K线 / 持仓后给出 JSON 决策
```

Pi 不直接拿 Binance 原始权限；执行必须经过 Python 风控闸门。

## 已实现脚本

- `scripts/pi_trading_supervisor.py`

## 能力

1. **持续盯盘**
   - 周期性读取账户、持仓、挂单、条件单。
   - 持仓时每轮都询问 Pi 是否需要调整。
   - 空仓时按 `--pi-interval` 节流询问机会。

2. **实时新闻**
   - RSS：CoinDesk、CoinTelegraph、Decrypt、Bitcoin Magazine、WSJ Business、FT 中文。
   - NewsAPI：主流媒体 / 加密媒体关键词检索。
   - Tavily：实时 Web 搜索当前 crypto + macro 语境。
   - 根据持仓和候选币关键词筛选相关新闻。

3. **Binance 全面数据与机会发现**
   - 全市场 USDT 永续 ticker 扫描：涨跌幅、成交量、资金费率、涨跌家数。
   - 全市场榜单：成交量、涨幅、跌幅、负费率、正费率。
   - 聚焦币 Binance 1m / 5m / 15m K线。
   - RSI、EMA20、EMA60、近期高低点、趋势状态。
   - 聚焦币微观结构：盘口深度、买卖盘不平衡、点差、近期主动买卖、mark/index/funding。
   - 衍生品数据：open interest、OI history、global long/short、top trader long/short、taker long/short。
   - 扫描高成交量、高波动、资金费率、趋势和回调候选币。

4. **Pi 决策与持久上下文**
   - 默认模型：`openai-codex/gpt-5.5`（可用 `PI_TRADING_MODEL` 或 `--model` 覆盖）。
   - 通过 `pi --mode rpc --no-tools -c --session-dir state/pi_sessions` 启动。
   - 使用同一个持久 Pi 会话，决策与复盘会保留前后文。
   - Pi 只接收结构化快照。
   - Pi 只能输出 JSON 决策，不能调用工具或交易所。

5. **经常复盘**
   - 默认每 15 分钟向同一 Pi 会话追加一次复盘。
   - 每次非 `hold` 提案后强制立即复盘。
   - 复盘会读取 recent income / recent trades / 持仓 / 新闻 / K线。
   - 复盘只写入记忆和日志，不直接绕过风控执行。

6. **风控闸门**
   - 单次最多 1 个持仓。
   - 单笔最大风险默认账户 8%。
   - 最大杠杆 10x。
   - 开仓必须有 stop_loss / take_profit / invalid_if。
   - 开仓 confidence 必须 ≥ 0.74。
   - 所有 live 执行都由 Python 二次校验。

7. **执行能力**
   - `hold`
   - `close`
   - `reduce`
   - `tighten_stop`
   - `move_stop_to_breakeven`
   - `open_long`
   - `open_short`

## 运行方式

### 干跑验证

```bash
python3 scripts/pi_trading_supervisor.py --interval 60 --pi-interval 240 --review-interval 900
```

默认是 DRY-RUN，只记录 Pi 决策和风控结果，不会下单。

### 实盘运行

先停止旧 autopilot，避免两个进程同时管理仓位：

```bash
kill $(cat logs/autopilot_8h.pid)
```

然后启动：

```bash
nohup python3 scripts/pi_trading_supervisor.py --live --interval 60 --pi-interval 240 --review-interval 900 \
  >> logs/pi_supervisor.out 2>&1 & echo $! > logs/pi_supervisor.pid
```

## 输出

- 事件日志：`logs/pi_supervisor_*.jsonl`
- 最新状态：`state/pi_supervisor_state.json`
- Pi 持久会话：`state/pi_sessions/`
- nohup 输出：`logs/pi_supervisor.out`

## 安全边界

Pi RPC 启动参数包含：

```bash
--no-tools
```

同时使用持久 session 保存上下文：

```bash
-c --session-dir state/pi_sessions
```

所以 Pi 不能：

- 执行 bash
- 修改文件
- 直接调用 Binance
- 绕过 Python 风控

Pi 的角色是“分析和提案”，不是“裸执行”。
