# Pi 集成盯盘 Supervisor

## 目标

把现有“固定规则脚本”升级为：

```text
Python supervisor 负责采集 / 风控 / 执行
Pi RPC 负责阅读快照 / 新闻 / K线 / 持仓后给出 JSON 决策
```

Pi 不直接拿 Binance 原始权限；执行必须经过 Python 风控闸门。

## 双轨策略职责

系统目标调整为两条明确分离的策略轨道：

| 轨道 | 决策主导 | 周期 | 标的范围 | Supervisor 职责 |
|------|----------|------|----------|-----------------|
| `ai_tactical` | Pi / AI | 分钟至数小时 | 经筛选的高流动性币 | 持续扫描、提出中短线交易、管理保护单并复盘 |
| `user_thesis` | 用户给出核心方向，Pi / AI 提供信息验证与执行辅助 | 数小时至数天 | 优先 BTC / ETH，必要时 SOL | 保存 thesis、规划分批入场/退出、监视失效条件并维护保护 |

基本边界：

- AI 的优势用于快速整合多币行情、盘口、OI、资金费率、新闻和宏观数据，不等于持续交易所有候选币。
- `ai_tactical` 只在结构和流动性足以覆盖手续费与风险时开仓。
- `user_thesis` 必须记录用户原始判断、允许加仓条件、失效条件、最大总仓位、最大风险和止盈管理方案。
- 对 `user_thesis` 仓位，短周期噪音只能作为风险提示，不能单独触发与用户中长期判断冲突的反向交易或整仓退出。
- 两条轨道始终共享 Python 硬安全要求：受控执行、交易所保护单、风险上限、日志和故障保护。

当前实现状态：现有 supervisor 的自动执行路径仍属于 `ai_tactical`。`user_thesis` 的职责原则已写入文档和交易提示词；在增加独立的 thesis 输入与持仓标记字段之前，不应把现有自动开仓误认为用户授权的中长期趋势仓。

## 已实现脚本

- `scripts/pi_trading_supervisor.py`

## 能力

1. **持续盯盘**
   - 周期性读取账户、持仓、挂单、条件单。
   - live 模式有独立 10 秒保护巡检，不被行情扫描或 Pi 响应延迟阻塞。
   - 持仓时每轮都询问 Pi 是否需要调整。
   - 空仓时按 `--pi-interval` 节流询问机会。

2. **实时新闻 / 宏观 / 公告**
   - RSS：CoinDesk、CoinTelegraph、Decrypt、Bitcoin Magazine、WSJ Business、FT 中文。
   - NewsAPI：主流媒体 / 加密媒体关键词检索。
   - Tavily：实时 Web 搜索当前 crypto + macro 语境。
   - Binance 公告 CMS：新币、合约上线、下架、维护、产品支持等官方公告。
   - FRED：Fed Funds、2Y/10Y、实际利率、美元指数、VIX、信用利差等宏观状态。
   - 过滤后保留 `text`、matched keywords、link、published 等较完整信息，不再只给标题。

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
   - 通过 `pi --mode rpc --no-tools --no-context-files --append-system-prompt prompts/pi_trading_system.md -c --session-dir state/pi_sessions` 启动。
   - `AGENTS.md` 只用于项目构建说明，不再作为 Pi 交易策略来源。
   - `prompts/pi_trading_system.md` 才是 Pi 交易决策专用系统提示词。
   - 使用同一个持久 Pi 会话，决策与复盘会保留前后文。
   - Pi 只接收结构化快照。
   - Pi 只能输出 JSON 决策，不能调用工具或交易所。

5. **经常复盘**
   - 默认每 15 分钟向同一 Pi 会话追加一次复盘。
   - 每次非 `hold` 提案后强制立即复盘。
   - 复盘会读取 recent income / recent trades / 持仓 / 新闻 / K线。
   - 复盘只写入记忆和日志，不直接绕过风控执行。

6. **风控闸门（AI-forward 模式）**
   - 默认最多 3 个持仓。
   - 单笔最大风险默认账户 25%。
   - 继续采集日内 PnL 供 Pi 复盘，但默认不以日内亏损阈值否决新提案。
   - 最大杠杆 20x。
   - 新开仓名义金额默认至少 6U，并按 Binance stepSize 向上取整，避免低于最小名义。
   - 开仓必须有 stop_loss / take_profit / invalid_if。
   - `confidence` 与 RR 由 Pi 自主判断，Python 不以策略阈值替代 AI 决策。
   - 检查可用保证金，避免保证金不足。
   - 所有 live 执行仍由 Python 做最低限度硬校验。
   - 账户或订单读取不可信时拒绝破坏性操作与新开仓。
   - 不支持在同一 symbol 现有仓位上直接加仓；Pi 可管理原仓或寻找新的非冲突标的。

7. **执行能力**
   - `hold`
   - `close`
   - `reduce`
   - `tighten_stop`
   - `move_stop_to_breakeven`
   - `open_long`
   - `open_short`
   - 新开仓优先 post-only maker 限价，支持 Pi 提供 `entry_price` / `entry_zone`。
   - 成交后自动挂 STOP_MARKET 止损和 TAKE_PROFIT_MARKET 止盈。
   - 首次保护使用 close-all；替换已有止损时先创建可并存的 `reduceOnly + quantity` 新止损，确认成功后再撤旧止损；部分成交立刻取消余单并转入保护。
   - `move_stop_to_breakeven` 允许 Pi 请求比保本线更紧的止损；Python 只阻止退回保本线以下或放松现有止损。
   - 调止损会使用 `/fapi/v3/positionRisk` 的实时 `markPrice` 校验触发边界；缺少 mark 数据或会立即触发的提案会被拒绝。
   - 空仓时自动清理孤儿挂单；持仓缺少有效止损时自动恢复紧急止损。
   - 平仓只有确认仓位归零后才撤保护单。
   - 执行后会重新读取账户与保护单；真实执行结果、`all_open_algo_orders` 和数据健康状态一起回传给 Pi 复盘，而不是只告诉 Pi 风控是否通过。
   - live supervisor 与旧 autopilot 共用 `state/live_execution.lock`，同一时刻只能有一个执行进程。

## 运行方式

### 干跑验证

```bash
python3 scripts/pi_trading_supervisor.py --interval 60 --pi-interval 240 --review-interval 900
```

默认是 DRY-RUN，只记录 Pi 决策和风控结果，不会下单。

### 实盘运行

旧 autopilot 现在也会竞争执行锁；仍建议先停止它，使日志更清晰：

```bash
kill $(cat logs/autopilot_8h.pid)
```

然后启动：

```bash
PI_BINARY="$(command -v pi)" nohup python3 scripts/pi_trading_supervisor.py --live --interval 60 --pi-interval 60 --review-interval 900 \
  >> logs/pi_supervisor.out 2>&1 & echo $! > logs/pi_supervisor.pid
```

若交给 macOS `launchctl` 管理，必须显式提供 `PI_BINARY` 的绝对路径，因为服务环境通常没有 nvm 的交互式 `PATH`：

```bash
launchctl submit -l com.financepi.supervisor -- /bin/zsh -lc \
  'cd /Users/wu/Github/financePi && exec /usr/bin/env PI_BINARY=/Users/wu/.nvm/versions/node/v24.14.0/bin/pi /usr/bin/caffeinate -ims /usr/bin/python3 scripts/pi_trading_supervisor.py --live --interval 60 --pi-interval 60 --review-interval 900 >> logs/pi_supervisor.out 2>&1'
```

## 输出

- 事件日志：`logs/pi_supervisor_*.jsonl`
- 最新状态：`state/pi_supervisor_state.json`
- Pi 持久会话：`state/pi_sessions/`
- nohup 输出：`logs/pi_supervisor.out`

## 安全边界

Pi RPC 启动参数包含：

```bash
--no-tools --no-context-files --append-system-prompt prompts/pi_trading_system.md
```

含义：

- `--no-tools`：Pi 不能调用 bash / edit / Binance。
- `--no-context-files`：Pi 不读取项目 `AGENTS.md`，避免把工程构建指南混作交易策略。
- `--append-system-prompt prompts/pi_trading_system.md`：只使用专门的交易员系统提示词。

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

## 故障行为

- `SIGTERM` 到达后，即便 Pi 的在途响应稍后返回，或信号落在刷新/加锁窗口内，也不会落地新执行。
- 账户、持仓或保护单读取失败时，guard 会跳过撤单和新开仓，等待下一次可信读取。
- 新开仓成交后若无法创建交易所止损，supervisor 会请求紧急 reduce-only 平仓并记录高优先级事件。
- `scripts/monitor_and_protect.py` 是历史只读监视脚本，不会创建止损或止盈，不应用于 live 保护。
