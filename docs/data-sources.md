# 数据源与工具总结

本文档用于指导交易 Agent 获取数据、构建信号、解释事件，并在交易前进行风控约束。

核心原则：

```text
数据采集 → 因子计算 → 事件解释 → 交易提案 → 风控检查 → 执行/放弃 → 记录复盘
```

> 注意：本文只描述数据源与分析逻辑。实盘交易必须走受控工具与风控闸门，禁止裸调 Binance 下单接口。

---

## 一、Binance API

### 1. Base URL 区分

| 市场 | Base URL | 用途 |
|---|---|---|
| 现货 | `https://api.binance.com` | 现货价格、现货 K 线 |
| U 本位合约 | `https://fapi.binance.com` | 合约行情、资金费率、持仓量、账户仓位 |

本项目当前主要分析 U 本位合约，因此行情和 K 线优先使用 `fapi.binance.com`。

### 2. 现货价格

```bash
# 单个币种当前价格
curl "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"

# 单个币种 24h 行情
curl "https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT"
```

### 3. 合约行情

```bash
# 所有 U 本位合约 24h 行情
curl "https://fapi.binance.com/fapi/v1/ticker/24hr"

# 单个合约 24h 行情
curl "https://fapi.binance.com/fapi/v1/ticker/24hr?symbol=BTCUSDT"
```

常用字段：

```text
lastPrice              最新价
priceChangePercent     24h 涨跌幅
highPrice / lowPrice   24h 高低点
volume                 成交量，币本位数量
quoteVolume            成交额，USDT
count                  成交笔数
```

### 4. K 线数据

```bash
# 现货 K 线
curl "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=200"

# U 本位合约 K 线，交易分析优先使用这个
curl "https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=4h&limit=200"
```

常用周期：

```text
5m / 15m：短线入场观察
1h：短线趋势
4h：主交易周期
1d：中期方向
```

### 5. 资金费率

```bash
# 历史资金费率
curl "https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&limit=10"

# 实时资金费率，所有合约
curl "https://fapi.binance.com/fapi/v1/premiumIndex"
```

资金费率一般每 8 小时结算一次。年化参考：

```python
annual_rate = funding_rate * 3 * 365 * 100
```

阈值参考，单次 8h 资金费率：

| 资金费率 | 含义 |
|---:|---|
| `< 0` | 空头付费，多头收钱，市场偏空或空头拥挤 |
| `0% ~ 0.01%` | 正常 |
| `> 0.01%` | 多头略拥挤 |
| `> 0.03%` | 多头明显拥挤，谨慎追多 |
| `> 0.05%` | 过热，注意回调或多杀多 |
| `> 0.10%` | 极端异常，避免追高 |

### 6. 持仓量 OI

```bash
# 当前持仓量
curl "https://fapi.binance.com/fapi/v1/openInterest?symbol=BTCUSDT"

# 历史持仓量
curl "https://fapi.binance.com/futures/data/openInterestHist?symbol=BTCUSDT&period=5m&limit=30"
```

解释：

| 价格 | OI | 可能含义 |
|---|---|---|
| 上涨 | 上升 | 多头加杠杆，趋势强化，但也可能过热 |
| 上涨 | 下降 | 空头止损/平仓，逼空后需防回落 |
| 下跌 | 上升 | 空头加仓或多头被套，趋势偏空 |
| 下跌 | 下降 | 去杠杆/多头平仓，可能接近短线释放 |

### 7. 多空比与主动买卖量

```bash
# 全市场账户多空比
curl "https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol=BTCUSDT&period=5m&limit=30"

# 大户账户多空比
curl "https://fapi.binance.com/futures/data/topLongShortAccountRatio?symbol=BTCUSDT&period=5m&limit=30"

# 大户持仓多空比
curl "https://fapi.binance.com/futures/data/topLongShortPositionRatio?symbol=BTCUSDT&period=5m&limit=30"

# 主动买卖量比例
curl "https://fapi.binance.com/futures/data/takerlongshortRatio?symbol=BTCUSDT&period=5m&limit=30"
```

用途：

```text
判断多空是否拥挤
判断上涨/下跌是否由主动买盘或主动卖盘推动
辅助识别逼空、杀多、假突破
```

### 8. 深度与流动性

```bash
# 订单簿深度
curl "https://fapi.binance.com/fapi/v1/depth?symbol=BTCUSDT&limit=100"
```

用途：

```text
判断买卖盘厚度
估算滑点
过滤低流动性小币
```

### 9. 账户与仓位，只读优先

账户相关接口必须使用签名请求。AI 默认只能查询，不能直接下单。

常用只读接口：

```text
GET /fapi/v3/account
GET /fapi/v3/positionRisk
GET /fapi/v1/openOrders
```

必须禁止给 AI 暴露这种工具：

```text
binance_raw_api(method, path, params)
```

应该暴露受控工具：

```text
get_account_state()
get_positions()
get_open_orders()
propose_trade(...)
risk_check(proposal)
execute_approved_trade(approval_id)
```

---

## 二、新闻与事件数据源

### 1. RSS

#### 加密货币新闻

- CoinTelegraph: `https://cointelegraph.com/rss`
- CoinDesk: `https://www.coindesk.com/arc/outboundfeeds/rss/`

#### 宏观与全球新闻

- WSJ Business: `https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml`
- FT 中文网: `http://www.ftchinese.com/rss/feed`

### 2. NewsAPI / Tavily / DDGS

用途：

```text
RSS 覆盖不足时，按关键词搜索最新新闻
核对重大事件是否多源确认
获取事件背景与市场解读
```

建议查询主题：

```text
Bitcoin ETF outflow
Ethereum TVL
Federal Reserve rate cut
US CPI inflation
Treasury yields
Middle East oil risk
Binance regulation
SEC crypto
stablecoin depeg
```

### 3. 事件分级

| 级别 | 类型 | 例子 | 处理 |
|---|---|---|---|
| S | 系统性风险 | 交易所暴雷、稳定币脱锚、战争升级、重大监管禁令 | 暂停新开仓，检查全部风险 |
| A | 宏观重大事件 | CPI、PCE、FOMC、非农、美债收益率大幅波动 | 降低杠杆，等待波动释放 |
| B | 行业重大事件 | ETF 大额流入/流出、黑客攻击、SEC 起诉 | 影响相关板块 |
| C | 叙事事件 | AI/RWA/DePIN/Layer2 热点新闻 | 只作为辅助，不单独重仓 |
| D | 噪音 | 单一 KOL 观点、软文、项目 PR | 记录但不交易 |

### 4. 新闻关键词

```python
ai_keywords = [
    'ai', 'artificial intelligence', 'machine learning',
    'worldcoin', 'wld', 'fetch', 'fet', 'render', 'tao'
]

rwa_keywords = [
    'rwa', 'real world asset', 'tokenized', 'tokenization',
    'ondo', 'plume', 'private credit', 'treasury'
]

defi_keywords = [
    'defi', 'decentralized finance', 'dex', 'amm',
    'liquidity', 'staking', 'tvl', 'yield'
]

l2_keywords = [
    'layer 2', 'l2', 'rollup', 'optimism', 'arbitrum',
    'base', 'zksync', 'starknet'
]

reg_keywords = [
    'sec', 'cftc', 'regulation', 'regulatory', 'compliance',
    'lawsuit', 'ban', 'etf', 'approval', 'rejection'
]

macro_keywords = [
    'fed', 'fomc', 'interest rate', 'inflation', 'cpi', 'pce',
    'gdp', 'employment', 'nonfarm payrolls', 'tariff', 'trade war',
    'recession', 'treasury yield', 'dollar index'
]

risk_keywords = [
    'hack', 'exploit', 'depeg', 'insolvency', 'liquidation',
    'bankruptcy', 'withdrawal halt', 'sanction'
]
```

---

## 三、宏观数据源

### 1. FRED

适合低频宏观因子，不需要高频更新。

| 指标 | FRED 代码 | 用途 |
|---|---|---|
| 美国 10 年期国债收益率 | `DGS10` | 风险资产估值压力 |
| 美国 2 年期国债收益率 | `DGS2` | 利率预期 |
| 10Y-2Y 利差 | `T10Y2Y` | 衰退预期 |
| 联邦基金利率 | `FEDFUNDS` | 政策利率 |
| CPI | `CPIAUCSL` | 通胀 |
| 失业率 | `UNRATE` | 就业周期 |
| VIX | `VIXCLS` | 市场风险偏好 |
| 美元指数相关 | `DTWEXBGS` | 美元强弱 |

### 2. 使用方式

```text
宏观数据不用于秒级交易
主要用于判断：
- 美股是否处于顺风环境
- BTC 是否受流动性压制
- 当前是否适合降低杠杆
```

---

## 四、链上与 DeFi 数据

详细接口见：`docs/api-sources.md`。

### 1. DefiLlama

常用数据：

```text
链 TVL
协议 TVL
稳定币供应
DeFi 收益率
资产价格
```

用途：

```text
判断 ETH / SOL / DeFi 板块基本面
观察稳定币供应是否扩张
确认 RWA、LST、借贷等叙事热度
```

### 2. 重点观察指标

| 指标 | 含义 |
|---|---|
| 稳定币总供应上升 | 加密市场流动性改善 |
| 稳定币总供应下降 | 资金流出或风险偏好下降 |
| ETH TVL 下降 | ETH/DeFi 基本面走弱 |
| Solana TVL 上升 | SOL 生态相对强 |
| RWA 协议 TVL 上升 | RWA 叙事增强 |

---

## 五、技术指标计算

### 1. SMA

```python
def sma(data, period):
    if len(data) < period:
        return None
    return sum(data[-period:]) / period
```

### 2. EMA

```python
def ema(data, period):
    if len(data) < period:
        return None
    k = 2 / (period + 1)
    e = data[0]
    for v in data[1:]:
        e = v * k + e * (1 - k)
    return e
```

常用：

```text
EMA20：短期趋势
EMA50：中期趋势
EMA200：长期趋势
```

### 3. RSI

```python
def rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for a, b in zip(closes[:-1], closes[1:]):
        d = b - a
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for g, l in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
    if avg_loss == 0:
        return 100
    return 100 - 100 / (1 + avg_gain / avg_loss)
```

参考：

```text
RSI < 30：超卖，但不等于立刻买
RSI 40-50：偏弱震荡
RSI 50-65：健康上涨
RSI > 70：过热，谨慎追多
RSI > 80：极端过热
```

### 4. ATR

```python
def atr(rows, period=14):
    # rows: [{'high': ..., 'low': ..., 'close': ...}, ...]
    if len(rows) < period + 1:
        return None
    trs = []
    prev_close = rows[0]['close']
    for r in rows[1:]:
        tr = max(
            r['high'] - r['low'],
            abs(r['high'] - prev_close),
            abs(r['low'] - prev_close),
        )
        trs.append(tr)
        prev_close = r['close']
    value = sum(trs[:period]) / period
    for tr in trs[period:]:
        value = (value * (period - 1) + tr) / period
    return value
```

用途：

```text
估算合理止损距离
计算仓位大小
判断当前波动是否异常
```

### 5. 成交量比

```python
def volume_ratio(volumes, period=20):
    if len(volumes) < period + 1:
        return None
    return volumes[-1] / (sum(volumes[-period-1:-1]) / period)
```

参考：

```text
> 1.2：略放量
> 2.0：明显放量
> 5.0：异常放量，可能是事件驱动或爆仓
```

---

## 六、信号因子

### 1. 趋势因子

| 条件 | 含义 |
|---|---|
| 价格 > EMA20 > EMA50 | 短中期多头 |
| 价格 < EMA20 < EMA50 | 短中期空头 |
| 价格 > 日线 EMA20 | 中期偏强 |
| 价格 < 日线 EMA20 | 中期偏弱 |

### 2. 动量因子

| 条件 | 含义 |
|---|---|
| 价格突破近 20 根 K 线高点 | 突破信号 |
| 价格跌破近 20 根 K 线低点 | 破位信号 |
| RSI 50-65 | 健康上涨 |
| RSI 70+ | 过热 |
| RSI 30-40 | 弱势或超卖反弹区 |

### 3. 拥挤度因子

| 条件 | 含义 |
|---|---|
| 涨幅大 + 资金费率高 | 多头拥挤，回调风险 |
| 涨幅大 + 资金费率负 | 空头挤压，可能继续涨 |
| 跌幅大 + 资金费率高 | 多头被套，杀多风险 |
| 跌幅大 + 资金费率负 | 空头主导，可能继续跌，但注意反抽 |
| 价格涨 + OI 大增 | 杠杆多头增加，趋势或过热 |
| 价格跌 + OI 大增 | 杠杆空头增加或多头被套 |

### 4. 事件因子

| 事件 | 倾向 |
|---|---|
| ETF 大额流入 | BTC/ETH 偏多 |
| ETF 大额流出 | BTC/ETH 偏空 |
| 稳定币供应扩张 | 加密流动性偏多 |
| 稳定币脱锚 | 全市场风险 |
| 黑客攻击 | 相关协议/链偏空 |
| 美债收益率上升 | 风险资产承压 |
| 降息预期增强 | 美股与加密偏多 |

---

## 七、多空信号矩阵

### 1. 趋势延续多头

```text
价格 > EMA20 > EMA50
RSI 50-65
成交量比 > 1.2
资金费率不极端
新闻无明显利空
```

适合：回踩 EMA20/EMA50 企稳，或突破近期高点后跟随。

### 2. 反弹做空

```text
价格 < EMA20 < EMA50
反弹接近 EMA20/EMA50
RSI 40-50
资金费率仍为正或 OI 未明显下降
新闻偏空
```

适合：BTC/ETH/SOL 等主流币弱势反弹。

### 3. 过热回避或反手观察

```text
24h 涨幅 > 10%
RSI > 75
成交量比 > 3
资金费率 > 0.03%
```

处理：不追高。等待回踩、横盘消化，或出现跌破短周期支撑后再考虑空。

### 4. 超跌反弹观察

```text
24h 跌幅 < -8%
RSI < 30
成交量明显放大
资金费率转负
价格不再创新低
```

处理：只适合小仓试探，必须有止损，不可摊平亏损。

---

## 八、交易提案格式

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
  "reason": "ETH 站上 EMA20，资金费率正常，新闻面无明显利空。",
  "invalid_if": "跌破 2090 或 BTC 同步跌破关键支撑"
}
```

---

## 十、分析流程

```text
1. 获取市场快照
   - BTC / ETH / SOL / 当前持仓标的
   - Top volume 合约

2. 计算技术指标
   - EMA20 / EMA50 / EMA200
   - RSI14
   - ATR14
   - 成交量比
   - 近 20/50 根 K 线高低点

3. 获取合约衍生数据
   - 资金费率
   - OI
   - 多空比
   - 主动买卖量

4. 获取新闻与宏观事件
   - RSS
   - NewsAPI / Tavily
   - FRED 低频宏观

5. 生成候选机会
   - 趋势多头
   - 反弹做空
   - 过热回避
   - 超跌反弹

6. 生成交易提案
   - 入场
   - 止损
   - 止盈
   - 仓位
   - 失效条件

7. 风控检查
   - 通过才允许执行
   - 不通过则记录原因

8. 记录日志与复盘
   - 原始数据摘要
   - AI 判断
   - 风控结果
   - 成交结果
   - 后续复盘
```

---

## 十一、数据保存建议

长期运行状态不应只依赖 Pi 会话，应写入外部存储：

```text
SQLite / DuckDB / JSONL
```

建议保存：

```text
行情快照
技术指标
资金费率
OI 与多空比
新闻原文与摘要
事件分类
交易提案
风控结果
订单与成交
持仓变化
每日总结
复盘结论
```

---

## 十二、注意事项

1. Binance API 有频率限制，批量扫描需限速与缓存。
2. RSS 可能延迟或失效，重要新闻要多源确认。
3. 技术指标基于历史数据，不能保证预测未来。
4. 小币流动性差，滑点和插针风险高。
5. 资金费率是拥挤度指标，不是单独买卖信号。
6. 宏观数据适合判断大方向，不适合秒级交易。