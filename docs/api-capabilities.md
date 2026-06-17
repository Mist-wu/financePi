# API 能力全景手册

> 更新时间：2026-06-17  
> 用途：记录本项目可用数据源的全部端点、返回信息与限制  
> 验证：对各 API 做真实探测；Binance 现货于 2026-06-17 完成专项实测

---

## 0. 总览

### 0.1 密钥状态

| 变量 | 状态 | 说明 |
|------|------|------|
| `BINANCE_API_KEY` + `BINANCE_PRIVATE_KEY_PATH` | ✅ | Ed25519 签名有效；合约读写正常 |
| `NEWSAPI_API_KEY` | ✅ | 三端点正常 |
| `TAVILY_API_KEY` | ✅ | Search / Extract / Usage 正常（Development key） |
| `FRED_API_KEY` | ✅ | Series / Releases / Search 正常 |
| `EVEROS_API_KEY` | ✅ | 记忆读写/搜索正常；settings 404（账号未初始化） |

### 0.2 数据源一览

| 数据源 | Base URL | 密钥 | 主要用途 |
|--------|----------|------|----------|
| Binance 现货 | `https://api.binance.com` | ✅ | 现货价格、深度、K 线、账户、下单 |
| Binance U 本位合约 | `https://fapi.binance.com` | ✅ | 合约行情、衍生品、杠杆交易 |
| Binance 钱包 SAPI | `https://api.binance.com/sapi/` | ✅ | 币种信息、手续费、充提记录 |
| Binance 公告 CMS | `https://www.binance.com/bapi/` | 无 | 官方公告 |
| NewsAPI | `https://newsapi.org/v2/` | ✅ | 新闻检索 |
| Tavily | `https://api.tavily.com/` | ✅ | AI Web 搜索 / 抽取 / 研究 |
| FRED | `https://api.stlouisfed.org/fred/` | ✅ | 美国宏观经济数据 |
| EverOS | `https://api.evermind.ai` | ✅ | AI 持久记忆 |
| RSS | 各媒体 URL | 无 | 免费新闻 feed |
| DefiLlama | `api.llama.fi` 等 | 无 | 链上 TVL / 价格 / 稳定币 |

---

## 1. Binance 现货 API

**官方文档**：<https://developers.binance.com/docs/binance-spot-api-docs/rest-api>  
**Base URL**：`https://api.binance.com`  
**WebSocket**：`wss://stream.binance.com:9443` / `wss://data-stream.binance.vision`

### 1.1 认证

| 项 | 说明 |
|----|------|
| API Key | 请求头 `X-MBX-APIKEY` |
| 签名方式 | Ed25519（本项目）/ HMAC-SHA256 / RSA |
| Ed25519 签名 | 对 query 字符串签名 → Base64 → 追加 `signature` |
| 时间参数 | `timestamp`（毫秒）+ `recvWindow`（默认 5000） |
| 安全类型 | NONE（公开）/ TRADE（交易）/ USER_DATA（账户）/ USER_STREAM（数据流） |

**当前 Key 权限实测**：

| 能力 | 状态 |
|------|------|
| 公开行情 | ✅ 全部正常 |
| 签名只读（account / orders / trades） | ✅ 正常 |
| 现货下单（`order` / `order/test`） | ⚠️ HTTP 401，Key 未开现货交易权限 |
| 用户数据流（`userDataStream`） | ⚠️ HTTP 410 Gone（端点已废弃/迁移，需用 WebSocket API） |
| SAPI 钱包只读 | ✅ `capital/config/getall`、`asset/tradeFee` 正常 |

### 1.2 频率限制（来自 `exchangeInfo`）

| 类型 | 限制 |
|------|------|
| REQUEST_WEIGHT | **6000 / 分钟 / IP** |
| ORDERS | **100 / 10 秒** + **200,000 / 天** |
| RAW_REQUESTS | **300,000 / 5 分钟** |
| 超限 | HTTP 429；持续违规 → 418 IP 封禁 |

响应头：`X-MBX-USED-WEIGHT-1M`、`X-MBX-ORDER-COUNT-10S`、`X-MBX-ORDER-COUNT-1D`

### 1.3 公共端点（无需签名）— 实测 2026-06-17

#### 基础

| 端点 | 功能 | 返回字段 | 实测 |
|------|------|----------|------|
| `GET /api/v3/ping` | 连通性测试 | `{}` | ✅ |
| `GET /api/v3/time` | 服务器时间 | `serverTime` | ✅ |
| `GET /api/v3/exchangeInfo` | 交易规则 | `symbols[]`（filters、orderTypes、精度）、`rateLimits` | ✅ 3600+ 交易对 |

`exchangeInfo` 单 symbol 返回的 `orderTypes`：`LIMIT`、`LIMIT_MAKER`、`MARKET`、`STOP_LOSS`、`STOP_LOSS_LIMIT`、`TAKE_PROFIT`、`TAKE_PROFIT_LIMIT`；支持 OCO、冰山单、跟踪止损。

#### 行情数据

| 端点 | 功能 | 主要参数 | 返回字段 | 实测 |
|------|------|----------|----------|------|
| `GET /api/v3/depth` | 订单簿深度 | `symbol`, `limit`(5–5000) | `lastUpdateId`, `bids[]`, `asks[]` | ✅ |
| `GET /api/v3/trades` | 最近成交 | `symbol`, `limit`(≤1000) | `id`, `price`, `qty`, `quoteQty`, `time`, `isBuyerMaker` | ✅ |
| `GET /api/v3/historicalTrades` | 历史成交 | `symbol`, `limit`；需 API Key | 同上 + 更早 ID | ✅ |
| `GET /api/v3/aggTrades` | 聚合成交 | `symbol`, `startTime`, `endTime`, `limit` | `a`(aggId), `p`(价), `q`(量), `f/l`, `T`, `m`(是否卖方主动) | ✅ |
| `GET /api/v3/klines` | K 线 | `symbol`, `interval`, `startTime`, `endTime`, `limit`(≤1000) | `[开盘时间, 开, 高, 低, 收, 成交量, 收盘时间, 成交额, 笔数, 主动买量, 主动买额, 0]` | ✅ |
| `GET /api/v3/uiKlines` | UI 优化 K 线 | 同 klines | 同 klines 格式 | ✅ |
| `GET /api/v3/avgPrice` | 平均价格 | `symbol` | `mins`, `price`, `closeTime` | ✅ BTC ≈ 65844 |

**K 线周期**：`1s`, `1m`, `3m`, `5m`, `15m`, `30m`, `1h`, `2h`, `4h`, `6h`, `8h`, `12h`, `1d`, `3d`, `1w`, `1M`

#### 价格统计

| 端点 | 功能 | 主要参数 | 返回字段 | 实测 |
|------|------|----------|----------|------|
| `GET /api/v3/ticker/24hr` | 24h 统计 | `symbol` 或全市场 | `lastPrice`, `priceChange`, `priceChangePercent`, `highPrice`, `lowPrice`, `volume`, `quoteVolume`, `count` | ✅ |
| `GET /api/v3/ticker/price` | 最新价 | `symbol` 或全市场 | `symbol`, `price` | ✅ 全市场 3600 条 |
| `GET /api/v3/ticker/bookTicker` | 最优买卖 | `symbol` 或全市场 | `bidPrice`, `bidQty`, `askPrice`, `askQty` | ✅ |
| `GET /api/v3/ticker` | 滚动窗口行情 | `symbol`, `windowSize`(1d–7d), `type`(FULL/MINI) | 含 `openPrice`, `highPrice`, `lowPrice`, `lastPrice`, `volume` 等 | ✅ |
| `GET /api/v3/ticker/tradingDay` | 交易日行情 | `symbol`, `timeZone`(默认 UTC) | 同 ticker 结构 | ✅ |

### 1.4 私有端点（需签名，只读）

| 端点 | 功能 | 主要参数 | 返回字段 | 实测 |
|------|------|----------|----------|------|
| `GET /api/v3/account` | 账户信息 | — | `balances[]`（`asset`, `free`, `locked`）、`canTrade`, `canWithdraw`, `commissionRates` | ✅ |
| `GET /api/v3/account/commission` | 手续费率 | `symbol` | `standardCommission`（maker/taker/buyer/seller） | ✅ maker 0.1% |
| `GET /api/v3/order` | 单笔订单 | `symbol` + `orderId` 或 `origClientOrderId` | 订单详情（`status`, `price`, `executedQty` 等） | ✅ 端点可达 |
| `GET /api/v3/openOrders` | 当前挂单 | `symbol`（可选） | 挂单数组 | ✅ 0 笔 |
| `GET /api/v3/allOrders` | 历史订单 | `symbol`, `limit`, `startTime`, `endTime` | 订单数组 | ✅ |
| `GET /api/v3/myTrades` | 成交记录 | `symbol`, `limit`, `fromId` | `id`, `price`, `qty`, `commission`, `isBuyer` 等 | ✅ |
| `GET /api/v3/orderList` | OCO 订单 | `orderListId` 或 `origClientOrderId` | OCO 详情 | ✅ 端点可达 |
| `GET /api/v3/openOrderList` | 当前 OCO | — | OCO 数组 | — |
| `GET /api/v3/allOrderList` | 历史 OCO | `limit`, `startTime`, `endTime` | OCO 数组 | — |
| `GET /api/v3/rateLimit/order` | 订单频率 | — | 当前订单计数 | ⚠️ 401 无权限 |

### 1.5 交易端点（需签名 + 现货交易权限）

| 端点 | 方法 | 功能 | 主要参数 |
|------|------|------|----------|
| `/api/v3/order` | POST | 下单 | `symbol`, `side`, `type`, `quantity`/`quoteOrderQty`, `price`, `timeInForce`, `stopPrice` 等 |
| `/api/v3/order/test` | POST | 测试下单（不执行） | 同 order |
| `/api/v3/order` | DELETE | 撤单 | `symbol`, `orderId` 或 `origClientOrderId` |
| `/api/v3/openOrders` | DELETE | 撤销全部挂单 | `symbol`（可选） |
| `/api/v3/order/cancelReplace` | POST | 撤单并下新单 | 撤单参数 + 新单参数 |
| `/api/v3/order/oco` | POST | OCO 下单 | `symbol`, `side`, `quantity`, `price`, `stopPrice`, `stopLimitPrice` 等 |
| `/api/v3/orderList/oco` | DELETE | 撤销 OCO | `symbol`, `orderListId` |
| `/api/v3/orderList/oto` | POST | OTO（一触发另一） | — |
| `/api/v3/orderList/opo` | POST | OPO | — |

**订单类型**：

| type | 说明 |
|------|------|
| `LIMIT` | 限价单 |
| `LIMIT_MAKER` | Post-Only 限价 |
| `MARKET` | 市价单 |
| `STOP_LOSS` | 止损市价 |
| `STOP_LOSS_LIMIT` | 止损限价 |
| `TAKE_PROFIT` | 止盈市价 |
| `TAKE_PROFIT_LIMIT` | 止盈限价 |

**timeInForce**：`GTC`（一直有效）、`IOC`（立即成交否则取消）、`FOK`（全部成交否则取消）

> 当前 Key 未开现货交易权限，`order/test` 返回 401。开启 Binance API Management 中 **Enable Spot & Margin Trading** 后方可使用。

### 1.6 用户数据流

| 端点 | 方法 | 功能 | 实测 |
|------|------|------|------|
| `/api/v3/userDataStream` | POST | 创建 listenKey | ⚠️ HTTP 410 Gone |
| `/api/v3/userDataStream` | PUT | 续期 listenKey | — |
| `/api/v3/userDataStream` | DELETE | 关闭 listenKey | — |

> REST 版 `userDataStream` 已废弃。实时账户/订单推送请使用 [WebSocket API](https://developers.binance.com/docs/binance-spot-api-docs/web-socket-api) 或 WebSocket 数据流 `wss://stream.binance.com:9443/ws/<listenKey>`。

### 1.7 WebSocket 数据流（公开）

| 流 | 格式 | 数据 |
|----|------|------|
| `<symbol>@trade` | 逐笔成交 | 价格、数量、时间、是否买方主动 |
| `<symbol>@aggTrade` | 聚合成交 | 同 aggTrades |
| `<symbol>@kline_<interval>` | K 线 | OHLCV + 是否完结 |
| `<symbol>@depth` / `@depth@100ms` | 深度 | 增量订单簿 |
| `<symbol>@bookTicker` | 最优买卖 | bid/ask 价量 |
| `<symbol>@ticker` / `@miniTicker` | 24h 行情 | 完整/精简 |
| `!ticker@arr` | 全市场 ticker | 所有 symbol |

组合流：`<base>@<stream>/<stream>` 或 `/stream?streams=...`

### 1.8 SAPI 钱包接口（`https://api.binance.com/sapi/`）

需签名，属于账户级扩展接口。

| 端点 | 功能 | 返回信息 | 实测 |
|------|------|----------|------|
| `GET /sapi/v1/capital/config/getall` | 所有币种配置 | 充值/提现状态、网络、手续费、最小额 | ✅ 681 币种 |
| `GET /sapi/v1/asset/tradeFee` | 交易手续费 | 各 symbol maker/taker 费率 | ✅ |
| `GET /sapi/v3/asset/getUserAsset` | 用户资产 | 各币种余额与估值 | — |
| `GET /sapi/v1/capital/deposit/hisrec` | 充值记录 | 充值历史 | — |
| `GET /sapi/v1/capital/withdraw/history` | 提现记录 | 提现历史 | — |
| `GET /sapi/v1/asset/dribblet` | 小额兑换 | BNB 折算信息 | — |
| `POST /sapi/v1/asset/dust` | 粉尘兑换 | 小额资产转 BNB | — |
| `GET /sapi/v1/account/info` | 账户状态 | VIP 等级等 | — |
| `GET /sapi/v1/account/apiRestrictions` | API 权限 | 当前 Key 权限详情 | — |
| `GET /sapi/v1/account/apiTradingStatus` | 交易状态 | 是否被限制 | — |

### 1.9 现货 vs 合约：何时需要现货数据

| 场景 | 推荐数据源 |
|------|-----------|
| 合约交易主决策 | `fapi.binance.com`（标记价、资金费率、OI、盘口） |
| 期现价差 / 基差 | 合约侧 `premiumIndex`（含 `indexPrice`）或 `futures/data/basis` |
| 现货真实成交量 | 现货 `ticker/24hr` 的 `quoteVolume`（合约数据不含此维度） |
| 现货 ETF / 机构买盘 | 新闻 API（NewsAPI / Tavily），非行情 API |

---

## 2. Binance U 本位合约 API

**官方文档**：<https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info>  
**Base URL**：`https://fapi.binance.com`  
**WebSocket**：`wss://fstream.binance.com`

### 2.1 认证

同现货 Ed25519 签名；权限需 **Enable Reading** + **Enable Futures**。

### 2.2 频率限制

| 类型 | 限制 |
|------|------|
| REQUEST_WEIGHT | **2400 / 分钟 / IP** |
| ORDERS | **1200 / 分钟** + **300 / 10 秒** |
| `/futures/data/*` | **1000 / 5 分钟 / IP**（独立于 weight） |

### 2.3 公共端点

#### 基础

| 端点 | 功能 |
|------|------|
| `GET /fapi/v1/ping` | 连通性 |
| `GET /fapi/v1/time` | 服务器时间 |
| `GET /fapi/v1/exchangeInfo` | 合约规则（tickSize、stepSize、minNotional） |

#### 行情 / K 线

| 端点 | 功能 | 返回字段 |
|------|------|----------|
| `GET /fapi/v1/ticker/24hr` | 24h 行情 | `lastPrice`, `priceChangePercent`, `volume`, `quoteVolume` |
| `GET /fapi/v1/ticker/price` | 最新价 | `symbol`, `price` |
| `GET /fapi/v1/ticker/bookTicker` | 最优买卖 | `bidPrice`, `bidQty`, `askPrice`, `askQty` |
| `GET /fapi/v1/klines` | K 线 | OHLCV + taker 买卖量 |
| `GET /fapi/v1/continuousKlines` | 连续合约 K 线 | 同 klines |
| `GET /fapi/v1/indexPriceKlines` | 指数价格 K 线 | — |
| `GET /fapi/v1/markPriceKlines` | 标记价格 K 线 | — |
| `GET /fapi/v1/premiumIndex` | 溢价指数 | `markPrice`, `indexPrice`, `lastFundingRate`, `nextFundingTime` |
| `GET /fapi/v1/fundingRate` | 历史资金费率 | `fundingRate`, `fundingTime` |
| `GET /fapi/v1/depth` | 订单簿 | `bids[]`, `asks[]` |
| `GET /fapi/v1/trades` | 最近成交 | — |
| `GET /fapi/v1/aggTrades` | 聚合成交 | 含 taker 方向 `m` |

#### 持仓量 / 市场情绪

| 端点 | 功能 |
|------|------|
| `GET /fapi/v1/openInterest` | 当前 OI |
| `GET /futures/data/openInterestHist` | 历史 OI |
| `GET /futures/data/globalLongShortAccountRatio` | 全账户多空比 |
| `GET /futures/data/topLongShortAccountRatio` | 大户账户多空比 |
| `GET /futures/data/topLongShortPositionRatio` | 大户持仓多空比 |
| `GET /futures/data/takerlongshortRatio` | 主动买卖量比 |
| `GET /futures/data/basis` | 基差 |
| `GET /futures/data/premiumIndex` | 溢价指数统计 |

#### 其他

| 端点 | 功能 |
|------|------|
| `GET /fapi/v1/forceOrders` | 最近强平 |
| `GET /fapi/v1/insuranceBalance` | 保险基金余额 |
| `GET /fapi/v1/constituents` | 指数成分 |
| `GET /fapi/v1/assetIndex` | 多资产指数 |

### 2.4 私有端点（只读）

| 端点 | 功能 | 返回字段 |
|------|------|----------|
| `GET /fapi/v2/account` | 账户 | `totalWalletBalance`, `availableBalance`, `positions[]` |
| `GET /fapi/v3/account` | 账户 v3 | 更细 margin 拆分 |
| `GET /fapi/v3/balance` | 余额 | 各资产 `balance`, `availableBalance` |
| `GET /fapi/v3/positionRisk` | 持仓风险 | `markPrice`, `liquidationPrice`, `notional` |
| `GET /fapi/v1/openOrders` | 普通挂单 | — |
| `GET /fapi/v1/openAlgoOrders` | Algo 条件单 | — |
| `GET /fapi/v1/order` | 单笔订单 | — |
| `GET /fapi/v1/allOrders` | 历史订单 | — |
| `GET /fapi/v1/userTrades` | 成交 | — |
| `GET /fapi/v1/income` | 盈亏流水 | `COMMISSION`, `FUNDING_FEE`, `REALIZED_PNL` 等 |
| `GET /fapi/v1/leverageBracket` | 杠杆档位 | — |
| `GET /fapi/v1/adlQuantile` | ADL 分位 | — |
| `GET /fapi/v1/commissionRate` | 手续费率 | — |
| `GET /fapi/v1/apiTradingStatus` | 交易限制 | — |

### 2.5 交易端点

| 端点 | 方法 | 功能 |
|------|------|------|
| `/fapi/v1/order` | POST | 下单（`LIMIT`/`MARKET`/`STOP` 等） |
| `/fapi/v1/order` | DELETE | 撤单 |
| `/fapi/v1/order/test` | POST | 测试下单 |
| `/fapi/v1/batchOrders` | POST | 批量下单（≤5） |
| `/fapi/v1/allOpenOrders` | DELETE | 全撤 |
| `/fapi/v1/leverage` | POST | 设杠杆 |
| `/fapi/v1/marginType` | POST | 逐仓/全仓 |
| `/fapi/v1/positionSide/dual` | POST | 单向/双向持仓 |
| `/fapi/v1/algoOrder` | POST | Algo 条件单（止损/止盈/跟踪止损） |
| `/fapi/v1/algoOrder` | DELETE | 撤 Algo 单 |
| `/fapi/v1/listenKey` | POST/PUT/DELETE | 用户数据流 |

**合约订单类型**：`LIMIT`（含 `GTX` Post-Only）、`MARKET`、`STOP`、`STOP_MARKET`、`TAKE_PROFIT`、`TAKE_PROFIT_MARKET`、`TRAILING_STOP_MARKET`

### 2.6 WebSocket

| 流 | 数据 |
|----|------|
| `<symbol>@markPrice` | 标记价 + 资金费率 |
| `<symbol>@kline_<interval>` | K 线 |
| `<symbol>@depth` | 深度 |
| `<symbol>@aggTrade` | 聚合成交 |
| `!forceOrder@arr` | 全市场强平 |

---

## 3. Binance 公告 CMS

**Base URL**：`https://www.binance.com/bapi/composite/v1/public/cms/article/list/query`  
**认证**：无（公开 BAPI）  
**请求头**：`clienttype: web`（Python urllib 可能 TLS 失败，建议 curl）

| 参数 | 说明 |
|------|------|
| `type` | 公告类型 |
| `pageNo` / `pageSize` | 分页 |

**返回**：`data.catalogs[].articles[]` — `title`, `code`, `releaseDate`  
**用途**：新币上线、合约上线、下架、维护、活动  
**实测**：✅ `success=true`，可读取 160+ 条公告

---

## 4. NewsAPI

**官方文档**：<https://newsapi.org/docs>  
**Base URL**：`https://newsapi.org/v2/`  
**认证**：`apiKey` 参数或头 `X-Api-Key`

### 4.1 端点

#### `GET /v2/everything` — 全文检索（150,000+ 来源）

| 参数 | 说明 |
|------|------|
| `q` | 关键词（支持 `AND/OR/NOT`、引号、`+/-`） |
| `qInTitle` | 仅搜标题 |
| `searchIn` | `title` / `description` / `content` |
| `sources` | 来源 ID，最多 20 个 |
| `domains` / `excludeDomains` | 域名过滤 |
| `from` / `to` | ISO 8601 时间窗口 |
| `language` | `en`, `zh` 等 |
| `sortBy` | `relevancy` / `popularity` / `publishedAt` |
| `pageSize` | 1–100 |
| `page` | 分页 |

**返回**：`status`, `totalResults`, `articles[]` — `source`, `author`, `title`, `description`, `url`, `urlToImage`, `publishedAt`, `content`（截断约 200 字符）

#### `GET /v2/top-headlines` — 头条

| 参数 | 说明 |
|------|------|
| `country` | 2 字母国家码 |
| `category` | `business` / `technology` / `science` 等 7 类 |
| `sources` | 来源 ID（与 country/category 互斥） |
| `q` | 关键词 |
| `pageSize` / `page` | 分页 |

#### `GET /v2/top-headlines/sources` — 来源目录

返回 125 个来源的 `id`, `name`, `description`, `url`, `category`, `language`, `country`。

### 4.2 计划限制

| 项 | Developer（$0） | Business（$449/月） |
|----|-----------------|---------------------|
| 请求量 | **100 次/天** | 250,000 次/月 |
| 时效 | **24h 延迟** | 实时 |
| 历史 | 1 个月 | 5 年 |
| 场景 | 开发/测试 | 生产 |

**错误码**：`apiKeyExhausted`, `rateLimited`(429), `apiKeyInvalid`, `parametersMissing`

---

## 5. Tavily API

**官方文档**：<https://docs.tavily.com/llms.txt>  
**Base URL**：`https://api.tavily.com/`  
**认证**：`Authorization: Bearer <key>`

### 5.1 端点

| 端点 | 方法 | 功能 | 计费 |
|------|------|------|------|
| `/search` | POST | AI 优化 Web 搜索 | 1–2 credits |
| `/extract` | POST | URL 正文抽取 | 1–2 credits / 5 URL |
| `/map` | POST | 站点 URL 发现 | 1–2 credits / 10 页 |
| `/crawl` | POST | Map + Extract | 叠加 |
| `/research` | POST | 异步深度研究 | 4–250 credits |
| `/research/{id}` | GET | 轮询研究任务 | 计入 RPM |
| `/usage` | GET | 用量统计 | 免费 |

### 5.2 Search 参数

| 参数 | 选项 | 说明 |
|------|------|------|
| `search_depth` | `basic` / `advanced` / `fast` / `ultra-fast` | basic=1 credit |
| `max_results` | 0–20 | 返回条数 |
| `topic` | `general` / `news` / `finance` | **news 返回 `published_date`** |
| `time_range` | `day` / `week` / `month` / `year` | 时间过滤 |
| `include_answer` | `false` / `true` / `basic` / `advanced` | LLM 摘要 |
| `include_raw_content` | bool / `markdown` / `text` | 全文 |
| `include_domains` / `exclude_domains` | 数组 | 域名过滤 |

### 5.3 密钥类型

| 维度 | Development (`tvly-dev-*`) | Production (`tvly-prod-*`) |
|------|---------------------------|---------------------------|
| RPM | 100 | 1000 |
| 月度 credits | 1000 | 随计划 |
| 生产 | 仅开发/测试 | 需付费/PAYGO |

---

## 6. FRED API

**官方文档**：<https://fred.stlouisfed.org/docs/api/fred/>  
**Base URL**：`https://api.stlouisfed.org/fred/`  
**认证**：`api_key`（32 位小写 alphanumeric）

### 6.1 Series

| 端点 | 功能 |
|------|------|
| `fred/series` | 序列元数据 |
| `fred/series/observations` | 观测值（核心） |
| `fred/series/search` | 关键词搜索 |
| `fred/series/search/tags` | tag 过滤 |
| `fred/series/tags` | 序列 tag |
| `fred/series/updates` | 增量更新索引（~20 万条） |
| `fred/series/vintagedates` | ALFRED 修订历史 |

**observations 返回**：`date`, `value`（缺失为 `"."`）

### 6.2 Categories

`category`, `category/children`（根 id=0 → 8 大类）, `category/series`, `category/tags`

### 6.3 Releases

| 端点 | 功能 |
|------|------|
| `fred/releases` | 全部 release（~329 个） |
| `fred/release/dates` | 发布日历 |
| `fred/release/series` | release 下 series |
| `fred/v2/release/observations` | V2 批量拉取 |

### 6.4 Tags / Sources / Maps

- **Tags**：`fred/tags`, `fred/tags/series`
- **Sources**：121 个数据来源
- **Maps**：州级地理数据
- **ALFRED**：`realtime_start/end` 查询历史时点快照

### 6.5 常用宏观序列

| ID | 含义 | 频率 |
|----|------|------|
| `DFF` | 有效联邦基金利率 | 日 |
| `DGS2` / `DGS10` | 2Y / 10Y 国债收益率 | 日 |
| `T10Y2Y` | 10Y-2Y 利差 | 日 |
| `DFII10` | 10Y 实际利率（TIPS） | 日 |
| `T10YIE` | 10Y 盈亏平衡通胀 | 日 |
| `CPIAUCSL` / `CPILFESL` | CPI / Core CPI | 月 |
| `UNRATE` / `PAYEMS` | 失业率 / 非农 | 月 |
| `VIXCLS` | VIX | 日 |
| `DTWEXBGS` | 广义美元指数 | 日/周 |
| `BAMLH0A0HYM2` | 高收益债 OAS | 日 |
| `WALCL` / `M2SL` | Fed 资产负债表 / M2 | 周/月 |
| `NFCI` / `STLFSI4` | 金融条件 / 压力指数 | 周 |

### 6.6 频率限制

- V1：约 120 req/min；超限 HTTP 429
- V2：约 2 req/s

---

## 7. EverOS / EverMind API

**官方文档**：<https://docs.evermind.ai/llms.txt>  
**Base URL**：`https://api.evermind.ai`  
**认证**：`Authorization: Bearer <EVEROS_API_KEY>`

### 7.1 记忆（Memories）

| 端点 | 功能 |
|------|------|
| `POST /api/v1/memories` | 个人记忆写入（`user_id` + `messages[]`） |
| `POST /api/v1/memories/group` | 群组记忆 |
| `POST /api/v1/memories/agent` | Agent 轨迹（含 `tool` role） |
| `POST /api/v1/memories/flush` | 强制抽取 |
| `POST /api/v1/memories/get` | 结构化读取 |
| `POST /api/v1/memories/search` | 检索 |
| `POST /api/v1/memories/delete` | 删除 |

**记忆类型**：`episodic_memory`, `profile`, `foresight`, `eventlog`, `agent_case`, `agent_skill`

**检索方法**：`keyword`（<100ms）, `vector`, `hybrid`（推荐）, `agentic`（2–5s）

### 7.2 其他

| 类别 | 端点 | 功能 |
|------|------|------|
| Groups | `POST/GET/PATCH /api/v1/groups` | 群组管理 |
| Senders | `POST/GET/PATCH /api/v1/senders` | 参与者身份 |
| Tasks | `GET /api/v1/tasks/{id}` | 异步任务（TTL 1h） |
| Object | `POST /api/v1/object/sign` | S3 预签名上传 |
| Settings | `GET/PUT /api/v1/settings` | LLM / 抽取配置 |

### 7.3 配额

- 按 **MemCell** 计费（约 10 条消息 → 1 MemCell）
- HTTP rate limit 未公布
- 单次 add 最多 500 条 message

---

## 8. 无密钥数据源

### 8.1 RSS

| 来源 | URL |
|------|-----|
| CoinDesk | `https://www.coindesk.com/arc/outboundfeeds/rss/` |
| CoinTelegraph | `https://cointelegraph.com/rss` |
| WSJ Business | `https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml` |
| FT 中文 | `http://www.ftchinese.com/rss/feed` |

### 8.2 DefiLlama

| Base URL | 端点 | 数据 |
|----------|------|------|
| `api.llama.fi` | `/protocols`, `/v2/chains`, `/protocol/{slug}` | 协议/链 TVL |
| `yields.llama.fi` | `/pools` | DeFi 收益率 |
| `coins.llama.fi` | `/prices/current/{ids}` | 资产价格 |
| `stablecoins.llama.fi` | `/stablecoins` | 稳定币供应 |

---

## 9. 能力对照矩阵

| 数据源 | 密钥 | 实时性 | 历史深度 | 主要瓶颈 |
|--------|------|--------|----------|----------|
| Binance 现货 | ✅ | 毫秒级 | K 线可回溯 | weight 6000/min；现货交易需额外权限 |
| Binance 合约 | ✅ | 毫秒级 | K 线/OI/资金费 | weight 2400/min；futures/data 1000/5min |
| Binance CMS | 无 | 分钟级 | 分页 | TLS 需 curl |
| NewsAPI | ✅ | 24h 延迟（免费） | 1 个月 | **100 次/日** |
| Tavily | ✅ | 秒级 | 无长期存档 | **1000 credits/月** |
| FRED | ✅ | 日/周频 | 数十年 | ~120 req/min |
| EverOS | ✅ | 异步秒–分钟 | 持久 MemCell | MemCell 配额 |
| RSS | 无 | 分钟级 | feed 内 | 覆盖有限 |
| DefiLlama | 无 | 小时级 | 历史 TVL | 大响应需缓存 |

---

## 10. Binance 现货实测汇总（2026-06-17）

| 类别 | 端点 | 结果 |
|------|------|------|
| 公共 | ping, time, exchangeInfo, depth, trades, historicalTrades, aggTrades, klines, uiKlines, avgPrice | ✅ 全部 200 |
| 公共 | ticker/24hr, ticker/price, ticker/bookTicker, ticker, ticker/tradingDay | ✅ 全部 200 |
| 公共 | ticker/price（全市场 3600 symbol） | ✅ |
| 私有只读 | account, account/commission, openOrders, allOrders, myTrades | ✅ |
| 私有只读 | order（无效 ID 返回 400，端点可达） | ✅ |
| 交易 | order/test | ⚠️ 401 无现货交易权限 |
| 数据流 | userDataStream POST | ⚠️ 410 Gone（已废弃） |
| SAPI | capital/config/getall, asset/tradeFee | ✅ |

---

## 11. 参考链接

| API | 文档 |
|-----|------|
| Binance Spot REST | <https://developers.binance.com/docs/binance-spot-api-docs/rest-api> |
| Binance Spot WebSocket | <https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams> |
| Binance USDT-M | <https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info> |
| Binance Ed25519 | <https://developers.binance.com/docs/binance-spot-api-docs/faqs/api_key_types> |
| Binance SAPI | <https://developers.binance.com/docs/wallet> |
| NewsAPI | <https://newsapi.org/docs> |
| Tavily | <https://docs.tavily.com/llms.txt> |
| FRED | <https://fred.stlouisfed.org/docs/api/fred/> |
| EverOS | <https://docs.evermind.ai/llms.txt> |
| DefiLlama | `docs/api-sources.md` |
