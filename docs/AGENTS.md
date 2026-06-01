# financePi 项目构建指南

本文件只用于指导编码助手维护和构建本项目；不要把这里当作 Pi 交易决策系统提示词。

Pi 交易决策专用提示词位于：

```text
prompts/pi_trading_system.md
```

`AGENTS.md` 与 `prompts/pi_trading_system.md` 的职责必须分离：

- `AGENTS.md`：项目工程、代码修改、安全边界、文档维护。
- `prompts/pi_trading_system.md`：Pi 在交易 supervisor 中的交易员角色、分析框架和决策规则。

## 当前架构

```text
Python Supervisor
  ├── 收集 Binance 行情 / 账户 / 订单 / 衍生品数据
  ├── 收集 RSS / NewsAPI / Tavily / Binance 公告 / FRED 宏观
  ├── 构造结构化 snapshot
  ├── 调用 Pi RPC 获取 JSON 决策
  ├── 执行 Python 风控闸门
  ├── 执行 Binance 受控下单 / 撤单 / 灾难止损 / 动态仓位管理
  └── 写入 logs 与 state

Pi RPC
  ├── 使用 --no-tools，不能调用 bash / edit / Binance
  ├── 使用 --no-context-files，避免读取本 AGENTS.md 作为交易提示
  ├── 使用 prompts/pi_trading_system.md 作为交易专用系统提示
  └── 输出 JSON 决策和复盘
```

## 安全边界

不要给 Pi 裸 Binance API 权限，例如不要实现：

```text
binance_raw_api(method, path, params)
```

Pi 只能通过结构化 snapshot 分析并输出 JSON。真实执行必须经过 Python 风控。

受控流程：

```text
发现机会
  ↓
收集新闻 + 行情 + 资金费率 + OI + 盘口 + 技术指标
  ↓
Pi 输出结构化交易提案
  ↓
Python 风控检查
  ↓
通过后执行 post-only maker 入场 / 灾难止损 / 动态仓位管理
  ↓
记录日志
  ↓
Pi 复盘
```

## 主要文件

- 主程序：`scripts/pi_trading_supervisor.py`
- Pi 交易提示词：`prompts/pi_trading_system.md`
- 最新状态：`state/pi_supervisor_state.json`
- Pi 会话：`state/pi_sessions/`
- 事件日志：`logs/pi_supervisor_*.jsonl`
- 运行文档：`docs/pi-supervisor.md`
- 交易日志：`docs/trade-log.md`

## 修改代码时的原则

- 不要让 AI 获得未受限的交易所权限。
- 风控必须在 Python 层强制执行，不能只靠提示词。
- live 下单必须记录日志。
- 开仓必须有 disaster_stop 或 stop_loss，以及 invalid_if；take_profit 可选，由 Pi 后续动态管理。
- 同方向加仓必须重新经过 Python 风控，不能放松旧止损，并按刷新后的总仓位维护灾难止损。
- 保护单更新必须先确认新止损有效，不能先撤掉最后一个有效止损。
- 账户/挂单读取不可信时不能清理保护单或执行新开仓。
- 只能有一个 live 执行进程持有 `state/live_execution.lock`。
- 新增执行能力时，要同时更新风控、日志和文档。
- 改动 `scripts/pi_trading_supervisor.py` 后必须运行：

```bash
python3 -m py_compile scripts/pi_trading_supervisor.py
python3 -m unittest discover -s tests -v
```

## 数据源备注

- DefiLlama API：`docs/api-sources.md`
- duckduckgo_search：<https://raw.githubusercontent.com/deedy5/ddgs/refs/heads/main/README.md>
- Binance Futures：<https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info>
- Fred：<https://raw.githubusercontent.com/mortada/fredapi/refs/heads/master/README.md>
- Tavily：<https://docs.tavily.com/llms.txt>
- NewsAPI：<https://newsapi.org/docs>

## RSS

- CoinDesk：<https://www.coindesk.com/arc/outboundfeeds/rss/>
- CoinTelegraph：<https://cointelegraph.com/rss>
- WSJ Business：<https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml>
- FT 中文：<http://www.ftchinese.com/rss/feed>