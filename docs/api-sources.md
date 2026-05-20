# API 数据源速查

本文档面向 AI 编码助手，记录本项目优先使用的数据源文档、常用端点、参数和响应形态。

## DefiLlama

> 默认只读本页即可，避免上下文冗余。如需核对完整 schema，再按需查询 `docs/defillama-api.json`。

注意：不同类别端点使用不同 Base URL。实现时以下方 Base URL 表为准。

### Base URLs

| 用途 | Base URL |
| --- | --- |
| 协议 / 链 TVL | `https://api.llama.fi` |
| 收益率 | `https://yields.llama.fi` |
| 价格 | `https://coins.llama.fi` |
| 稳定币 | `https://stablecoins.llama.fi` |

### 常用端点

#### 1. 获取全部协议 TVL

```http
GET https://api.llama.fi/protocols
```

用途：获取 DeFi 协议列表、TVL、链、分类等。适合筛选头部协议、观察 TVL 变化。

常见响应字段：

```json
[
  {
    "id": "2269",
    "name": "Binance CEX",
    "address": null,
    "symbol": "BNB",
    "url": "https://www.binance.com",
    "description": "...",
    "chain": "Multi-Chain",
    "category": "CEX",
    "chains": ["Ethereum", "BSC"],
    "tvl": 1000000000,
    "change_1d": 1.2,
    "change_7d": -3.4,
    "mcap": null
  }
]
```

#### 2. 获取所有链 TVL

```http
GET https://api.llama.fi/v2/chains
```

用途：获取各链 TVL，用于链上宏观因子，例如 Ethereum / Solana / Arbitrum TVL 变化。

常见响应字段：

```json
[
  {
    "gecko_id": "ethereum",
    "tvl": 50000000000,
    "tokenSymbol": "ETH",
    "cmcId": "1027",
    "name": "Ethereum",
    "chainId": 1
  }
]
```

#### 3. 获取单个协议历史 TVL

```http
GET https://api.llama.fi/protocol/{protocol_slug}
```

示例：

```http
GET https://api.llama.fi/protocol/lido
```

用途：获取单个协议的历史 TVL、链拆分、代币信息。

常见响应字段：

```json
{
  "id": "182",
  "name": "Lido",
  "symbol": "LDO",
  "category": "Liquid Staking",
  "chains": ["Ethereum"],
  "tvl": [
    {"date": 1704067200, "totalLiquidityUSD": 21000000000}
  ],
  "chainTvls": {
    "Ethereum": {
      "tvl": [
        {"date": 1704067200, "totalLiquidityUSD": 21000000000}
      ]
    }
  }
}
```

#### 4. 获取收益率池列表

```http
GET https://yields.llama.fi/pools
```

用途：获取 DeFi 收益率数据，用于判断稳定币收益率、链上风险偏好、资金迁移。

常见响应字段：

```json
{
  "status": "success",
  "data": [
    {
      "chain": "Ethereum",
      "project": "lido",
      "symbol": "STETH",
      "tvlUsd": 18899055914,
      "apyBase": 2.379,
      "apyReward": null,
      "apy": 2.379,
      "pool": "747c...",
      "stablecoin": false,
      "ilRisk": "no",
      "exposure": "single"
    }
  ]
}
```

#### 5. 获取当前价格

```http
GET https://coins.llama.fi/prices/current/{coin_ids}
```

`coin_ids` 格式：

- CoinGecko：`coingecko:bitcoin`
- Ethereum 合约：`ethereum:0x...`
- 多个资产用逗号分隔：`coingecko:bitcoin,coingecko:ethereum`

示例：

```http
GET https://coins.llama.fi/prices/current/coingecko:bitcoin,coingecko:ethereum
```

常见响应字段：

```json
{
  "coins": {
    "coingecko:bitcoin": {
      "price": 77135.53,
      "symbol": "BTC",
      "timestamp": 1779286204,
      "confidence": 0.99
    }
  }
}
```

#### 6. 获取历史价格

```http
GET https://coins.llama.fi/prices/historical/{timestamp}/{coin_ids}
```

示例：

```http
GET https://coins.llama.fi/prices/historical/1704067200/coingecko:bitcoin
```

常见响应字段与当前价格接口类似。

#### 7. 获取稳定币链上数据

```http
GET https://stablecoins.llama.fi/stablecoins
```

用途：观察稳定币总供应、链上流动性变化、资金流入流出。

常见响应字段：

```json
{
  "peggedAssets": [
    {
      "id": "1",
      "name": "Tether",
      "symbol": "USDT",
      "circulating": {
        "peggedUSD": 100000000000
      },
      "chainCirculating": {
        "Ethereum": {"current": {"peggedUSD": 50000000000}}
      }
    }
  ]
}
```

### 实现建议

- 使用 `httpx.AsyncClient` 或 `httpx.Client`，设置合理超时，例如 10-20 秒。
- DefiLlama 大接口响应可能很大，建议缓存结果，并在 CLI 中只打印摘要。
- 价格接口适合实时快照；TVL / 收益率接口适合低频更新。
- 对字段缺失保持容错：使用 `.get()`，并通过 Pydantic 模型做可选字段校验。
- 记录原始响应摘要到 JSONL / SQLite，便于复盘。

## Tavily

AI 友好文档：

- `https://docs.tavily.com/llms.txt`
- `https://docs.tavily.com/llms-full.txt`

建议 AI 编码助手优先读取 `llms.txt`，需要完整上下文时再读取 `llms-full.txt`。
