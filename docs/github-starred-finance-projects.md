# GitHub Star 中的量化 / 金融项目清单

来源：使用 `gh api --paginate '/users/Mist-wu/starred?per_page=100'` 检索当前账号 Star，共 57 个仓库，筛选其中与金融、量化、交易、加密货币、金融 AI 直接相关的项目。

更新时间：2026-05-21

## 项目列表

| # | 项目 | 类型 | 主要语言 | 简介 |
|---|---|---|---|---|
| 1 | [OpenBB-finance/OpenBB](https://github.com/OpenBB-finance/OpenBB) | 金融数据平台 | Python | 面向分析师、量化和 AI Agent 的金融数据平台 |
| 2 | [AI4Finance-Foundation/FinGPT](https://github.com/AI4Finance-Foundation/FinGPT) | 金融 LLM / 情绪分析 | Jupyter Notebook | 开源金融大模型、金融 NLP、情绪分析、投研助手 |
| 3 | [hummingbot/hummingbot](https://github.com/hummingbot/hummingbot) | 加密货币交易机器人 | Python | 高频、做市、套利、订单簿交易机器人框架 |
| 4 | [jesse-ai/jesse](https://github.com/jesse-ai/jesse) | 加密货币策略框架 | JavaScript / Python 生态 | 面向 crypto 的策略开发、回测和交易框架 |
| 5 | [freqtrade/freqtrade](https://github.com/freqtrade/freqtrade) | 加密货币交易机器人 | Python | 开源 crypto bot，策略、回测、实盘、Telegram 控制 |
| 6 | [georgezouq/awesome-ai-in-finance](https://github.com/georgezouq/awesome-ai-in-finance) | 金融 AI 资料库 | - | 金融市场中 LLM、深度学习、强化学习、量化工具清单 |
| 7 | [ZhuLinsen/daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis) | LLM 股票分析系统 | Python | A/H/美股行情、新闻、LLM 决策仪表盘、定时推送 |
| 8 | [PlaceNL2026/best-of-algorithmic-trading](https://github.com/PlaceNL2026/best-of-algorithmic-trading) | 量化交易资源榜单 | TypeScript | 算法交易、回测、技术分析、crypto bot 资源排行 |
| 9 | [binance/binance-connector-python](https://github.com/binance/binance-connector-python) | Binance API SDK | Python | Binance 官方 Python 公共 API 连接器 |
| 10 | [shiyu-coder/Kronos](https://github.com/shiyu-coder/Kronos) | 金融市场基础模型 | Python | 面向金融市场语言的 Foundation Model |
| 11 | [hsliuping/TradingAgents-CN](https://github.com/hsliuping/TradingAgents-CN) | 中文多智能体交易框架 | Python | 基于多智能体 LLM 的中文金融交易框架 |
| 12 | [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) | 多智能体金融交易框架 | Python | LLM Multi-Agent Financial Trading Framework |

## 第一批：3 个项目解读

### 1. OpenBB-finance/OpenBB

定位：金融数据与分析平台，面向分析师、量化研究员和 AI Agent。

可借鉴点：

- 把金融数据源统一封装成可查询接口。
- 适合作为 Agent 的“金融数据工具层”参考。
- 它覆盖股票、经济、衍生品、期权、固定收益、crypto 等多类资产。

对本项目启发：

```text
financePi 可以参考 OpenBB 的思路，把行情、新闻、宏观、链上、账户状态统一成工具层。
Pi Agent 不直接关心底层 API，只调用标准化工具。
```

可应用方向：

- 设计 `get_macro_context()`、`get_asset_context()`、`get_market_snapshot()` 这类统一工具。
- 后续如果扩展股票/宏观分析，可以考虑接入 OpenBB 作为数据源之一。
- 学习它的数据标准化方式，避免每个 API 返回结构都不一样。

---

### 2. AI4Finance-Foundation/FinGPT

定位：开源金融大模型项目，重点是金融 NLP、新闻理解、情绪分析、投研助手。

可借鉴点：

- 金融新闻和公告不是普通文本，需要金融语义理解。
- 情绪分析可以成为交易信号的一部分，但不能单独触发重仓交易。
- 金融 LLM 更适合做“事件因子提取”和“风险解释”，而不是直接裸下单。

对本项目启发：

```text
financePi 可以把新闻处理拆成：
新闻原文 → 事件提取 → 情绪/影响方向 → 置信度 → 交易假设。
```

可应用方向：

- 做 `extract_news_factors(news)` 工具。
- 输出结构化结果：资产、事件类型、情绪方向、影响时效、置信度。
- 把新闻因子写入 SQLite，供 Pi 长期复盘。

---

### 3. hummingbot/hummingbot

定位：开源 crypto 交易机器人，偏高频、做市、套利、订单簿策略。

可借鉴点：

- 它强调交易执行、订单管理、连接器、风控参数和策略模块化。
- 对订单簿、滑点、做市、交易所连接的处理很值得参考。
- 但它偏自动化策略和高频，不适合直接照搬成 LLM 自主交易。

对本项目启发：

```text
financePi 不需要变成高频 bot，但可以借鉴 hummingbot 的执行层思想：
策略/决策和交易所连接解耦，执行服务负责订单生命周期和风控。
```

可应用方向：

- 参考它的交易所 connector 思路，封装 Binance 执行服务。
- 增加 `get_orderbook()`、`estimate_slippage()`、`liquidity_check()`。
- 风控不只检查仓位，还要检查点差、深度、滑点。

## 后续批次候选

下一批可以解释：

```text
4. jesse-ai/jesse
5. freqtrade/freqtrade
6. georgezouq/awesome-ai-in-finance
```

再下一批：

```text
7. ZhuLinsen/daily_stock_analysis
8. PlaceNL2026/best-of-algorithmic-trading
9. binance/binance-connector-python
```

最后一批：

```text
10. shiyu-coder/Kronos
11. hsliuping/TradingAgents-CN
12. TauricResearch/TradingAgents
```
