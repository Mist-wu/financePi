#!/usr/bin/env python3
"""
Pi-driven trading supervisor for Binance USDT-M futures.

Design:
- Python is the body: market/news/account collection, persistence, risk gates, execution.
- Pi is the brain: periodic reasoning/review over supplied snapshots, strict JSON decisions only.
- Pi is started in RPC mode with --no-tools so it cannot call Binance, bash, or edit files.
- By default Pi uses a persistent session so reviews and decisions share one conversation context.

Default is DRY-RUN. Use --live only after checking logs and stopping other bots.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from cryptography.hazmat.primitives import serialization

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"
STATE_DIR = ROOT / "state"

BINANCE_BASE = "https://fapi.binance.com"
DEFAULT_MODEL = os.environ.get("PI_TRADING_MODEL", "openai-codex/gpt-5.5")

RSS_FEEDS = [
    ("coindesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("cointelegraph", "https://cointelegraph.com/rss"),
    ("decrypt", "https://decrypt.co/feed"),
    ("bitcoin_magazine", "https://bitcoinmagazine.com/.rss/full/"),
    ("wsj_business", "https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml"),
    ("ft_cn", "http://www.ftchinese.com/rss/feed"),
]

FRED_SERIES = {
    "DFF": "Effective Fed Funds Rate",
    "DGS2": "2Y Treasury Yield",
    "DGS10": "10Y Treasury Yield",
    "T10Y2Y": "10Y-2Y Treasury Spread",
    "DFII10": "10Y Real Yield",
    "DTWEXBGS": "Trade Weighted US Dollar Index Broad",
    "VIXCLS": "VIX",
    "BAMLH0A0HYM2": "US High Yield OAS",
}

SYMBOL_KEYWORDS = {
    "BTCUSDT": ["bitcoin", "btc"],
    "ETHUSDT": ["ethereum", "ether", "eth"],
    "SOLUSDT": ["solana", "sol"],
    "SEIUSDT": ["sei"],
    "DRIFTUSDT": ["drift"],
    "INJUSDT": ["injective", "inj"],
}


# ----------------------------- utilities -----------------------------


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_dotenv(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def log_json(path: Path, event: str, **data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": now_iso(), "event": event, **data}
    print(json.dumps(row, ensure_ascii=False), flush=True)
    with path.open("a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


def floor_to_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    return math.floor(value / step) * step


def decimals_from_step(step: float) -> int:
    if step >= 1:
        return 0
    text = f"{step:.16f}".rstrip("0")
    return len(text.split(".")[1]) if "." in text else 0


def fmt_step(value: float, step: float) -> str:
    return f"{floor_to_step(value, step):.{decimals_from_step(step)}f}"


def round_tick(value: float, tick: float) -> str:
    if tick <= 0:
        return str(value)
    return f"{round(value / tick) * tick:.{decimals_from_step(tick)}f}"


def ema(values: list[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return e


def rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) <= period:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0:
        return 100.0
    rs = ag / al
    return 100 - 100 / (1 + rs)


def extract_json_object(text: str) -> dict[str, Any]:
    """Extract first JSON object from model output."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find("{")
    if start < 0:
        raise ValueError("No JSON object in Pi response")
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start : i + 1])
    raise ValueError("Unclosed JSON object in Pi response")


# ----------------------------- Binance client -----------------------------


class BinanceClient:
    def __init__(self, api_key: str, private_key_path: Path, log_path: Path):
        self.api_key = api_key
        self.log_path = log_path
        with private_key_path.open("rb") as f:
            self.private_key = serialization.load_pem_private_key(f.read(), password=None)
        self.filters: dict[str, dict[str, float]] = {}

    def sign(self, query: str) -> str:
        return base64.b64encode(self.private_key.sign(query.encode())).decode()

    def signed_api(self, endpoint: str, params: Optional[dict[str, Any]] = None, method: str = "GET") -> Any:
        params = dict(params or {})
        params.setdefault("recvWindow", 5000)
        params["timestamp"] = int(time.time() * 1000)
        query = urllib.parse.urlencode(params)
        params["signature"] = self.sign(query)
        url = f"{BINANCE_BASE}{endpoint}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"X-MBX-APIKEY": self.api_key}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            try:
                return json.loads(body)
            except Exception:
                return {"code": e.code, "msg": body}

    def public_api(self, endpoint: str, params: Optional[dict[str, Any]] = None) -> Any:
        url = f"{BINANCE_BASE}{endpoint}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=20) as r:
            return json.loads(r.read().decode())

    def refresh_filters(self) -> dict[str, dict[str, float]]:
        info = self.public_api("/fapi/v1/exchangeInfo")
        out: dict[str, dict[str, float]] = {}
        for s in info.get("symbols", []):
            if s.get("contractType") != "PERPETUAL" or s.get("quoteAsset") != "USDT" or s.get("status") != "TRADING":
                continue
            lot = next((f for f in s.get("filters", []) if f.get("filterType") == "LOT_SIZE"), {})
            pf = next((f for f in s.get("filters", []) if f.get("filterType") == "PRICE_FILTER"), {})
            out[s["symbol"]] = {
                "stepSize": safe_float(lot.get("stepSize"), 1.0),
                "minQty": safe_float(lot.get("minQty"), 0.0),
                "tickSize": safe_float(pf.get("tickSize"), 0.00000001),
            }
        self.filters = out
        return out

    def account_state(self) -> dict[str, Any]:
        acc = self.signed_api("/fapi/v2/account")
        positions = []
        for p in acc.get("positions", []) if isinstance(acc, dict) else []:
            if safe_float(p.get("positionAmt")) != 0:
                positions.append(p)
        return {
            "wallet": safe_float(acc.get("totalWalletBalance")) if isinstance(acc, dict) else 0,
            "available": safe_float(acc.get("availableBalance")) if isinstance(acc, dict) else 0,
            "unrealized": safe_float(acc.get("totalUnrealizedProfit")) if isinstance(acc, dict) else 0,
            "positions": positions,
            "raw_ok": isinstance(acc, dict) and "code" not in acc,
        }

    def open_orders(self, symbol: Optional[str] = None) -> Any:
        params = {"symbol": symbol} if symbol else None
        return self.signed_api("/fapi/v1/openOrders", params)

    def open_algo_orders(self, symbol: Optional[str] = None) -> Any:
        params = {"symbol": symbol} if symbol else None
        return self.signed_api("/fapi/v1/openAlgoOrders", params)

    def recent_income(self, limit: int = 50) -> Any:
        return self.signed_api("/fapi/v1/income", {"limit": limit})

    def recent_trades(self, symbol: str, limit: int = 20) -> Any:
        return self.signed_api("/fapi/v1/userTrades", {"symbol": symbol, "limit": limit})

    def cancel_all_orders(self, symbol: str) -> Any:
        return self.signed_api("/fapi/v1/allOpenOrders", {"symbol": symbol}, "DELETE")

    def cancel_open_algo_orders(self, symbol: str) -> Any:
        orders = self.open_algo_orders(symbol)
        results = []
        for o in orders if isinstance(orders, list) else []:
            results.append(self.signed_api("/fapi/v1/algoOrder", {"symbol": o["symbol"], "algoId": o["algoId"]}, "DELETE"))
        return results

    def place_hard_stop(self, symbol: str, side: str, trigger_price: float) -> Any:
        f = self.filters.get(symbol) or self.refresh_filters()[symbol]
        self.cancel_all_orders(symbol)
        self.cancel_open_algo_orders(symbol)
        trigger = round_tick(trigger_price, f["tickSize"])
        res = self.signed_api(
            "/fapi/v1/algoOrder",
            {
                "symbol": symbol,
                "side": side,
                "algoType": "CONDITIONAL",
                "type": "STOP_MARKET",
                "triggerPrice": trigger,
                "closePosition": "true",
                "workingType": "MARK_PRICE",
            },
            "POST",
        )
        return res

    def set_leverage(self, symbol: str, leverage: int) -> Any:
        return self.signed_api("/fapi/v1/leverage", {"symbol": symbol, "leverage": leverage}, "POST")

    def market_order(self, symbol: str, side: str, qty: float, reduce_only: bool = False) -> Any:
        f = self.filters.get(symbol) or self.refresh_filters()[symbol]
        qty_s = fmt_step(abs(qty), f["stepSize"])
        params = {"symbol": symbol, "side": side, "type": "MARKET", "quantity": qty_s}
        if reduce_only:
            params["reduceOnly"] = "true"
        return self.signed_api("/fapi/v1/order", params, "POST")

    def order_status(self, symbol: str, order_id: int) -> Any:
        return self.signed_api("/fapi/v1/order", {"symbol": symbol, "orderId": order_id})

    def cancel_order(self, symbol: str, order_id: int) -> Any:
        return self.signed_api("/fapi/v1/order", {"symbol": symbol, "orderId": order_id}, "DELETE")

    def place_limit_maker_order(self, symbol: str, side: str, qty: float, price: float, reduce_only: bool = False) -> Any:
        f = self.filters.get(symbol) or self.refresh_filters()[symbol]
        qty_s = fmt_step(abs(qty), f["stepSize"])
        price_s = round_tick(price, f["tickSize"])
        params = {
            "symbol": symbol,
            "side": side,
            "type": "LIMIT",
            "timeInForce": "GTX",  # post-only maker on Binance futures
            "quantity": qty_s,
            "price": price_s,
        }
        if reduce_only:
            params["reduceOnly"] = "true"
        return self.signed_api("/fapi/v1/order", params, "POST")

    def place_limit_entry_with_wait(self, symbol: str, side: str, qty: float, wait_seconds: int = 35) -> Any:
        """Prefer maker entry to reduce fees. Cancel if not filled quickly.

        BUY posts at best bid; SELL posts at best ask. If unfilled, no chase.
        Risk exits still use market/stop because reducing loss has priority over maker fees.
        """
        depth = self.public_api("/fapi/v1/depth", {"symbol": symbol, "limit": 5})
        bids = [(safe_float(p), safe_float(q)) for p, q in depth.get("bids", [])]
        asks = [(safe_float(p), safe_float(q)) for p, q in depth.get("asks", [])]
        if not bids or not asks:
            return {"code": "NO_DEPTH", "msg": "No order book depth"}
        price = bids[0][0] if side == "BUY" else asks[0][0]
        res = self.place_limit_maker_order(symbol, side, qty, price, reduce_only=False)
        if not isinstance(res, dict) or "orderId" not in res:
            return res
        order_id = res["orderId"]
        deadline = time.time() + wait_seconds
        last = res
        while time.time() < deadline:
            time.sleep(2)
            last = self.order_status(symbol, order_id)
            if isinstance(last, dict) and last.get("status") in {"FILLED", "PARTIALLY_FILLED", "CANCELED", "EXPIRED", "REJECTED"}:
                if last.get("status") == "PARTIALLY_FILLED":
                    # Keep waiting for the remaining qty until timeout.
                    continue
                return last
        last = self.order_status(symbol, order_id)
        if isinstance(last, dict) and last.get("status") != "FILLED":
            cancel = self.cancel_order(symbol, order_id)
            final = self.order_status(symbol, order_id) if isinstance(cancel, dict) else last
            return {"initial": res, "last": last, "cancel": cancel, "final": final}
        return last

    def close_position_market(self, symbol: str, qty: float) -> Any:
        side = "SELL" if qty > 0 else "BUY"
        return self.market_order(symbol, side, abs(qty), reduce_only=True)

    def klines(self, symbol: str, interval: str = "1m", limit: int = 60) -> list[list[Any]]:
        return self.public_api("/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit})

    def indicators(self, symbol: str) -> dict[str, Any]:
        result: dict[str, Any] = {"symbol": symbol}
        for interval, limit in [("1m", 80), ("5m", 80), ("15m", 80)]:
            try:
                ks = self.klines(symbol, interval, limit)
                closes = [safe_float(k[4]) for k in ks]
                highs = [safe_float(k[2]) for k in ks]
                lows = [safe_float(k[3]) for k in ks]
                vols = [safe_float(k[5]) for k in ks]
                if not closes:
                    continue
                e20 = ema(closes, 20)
                e60 = ema(closes, 60)
                recent_high = max(highs[-20:]) if len(highs) >= 20 else max(highs)
                recent_low = min(lows[-20:]) if len(lows) >= 20 else min(lows)
                result[interval] = {
                    "last": closes[-1],
                    "rsi14": round(rsi(closes), 2),
                    "ema20": round(e20, 8) if e20 else None,
                    "ema60": round(e60, 8) if e60 else None,
                    "recent_high": recent_high,
                    "recent_low": recent_low,
                    "last_volume": vols[-1],
                    "trend": "up" if e20 and e60 and closes[-1] > e20 > e60 else "down" if e20 and e60 and closes[-1] < e20 < e60 else "mixed",
                }
            except Exception as e:
                result[interval] = {"error": str(e)}
        return result

    def market_overview(self) -> dict[str, Any]:
        """All-symbol Binance futures overview, summarized to fit Pi context."""
        tickers = self.public_api("/fapi/v1/ticker/24hr")
        premiums = self.public_api("/fapi/v1/premiumIndex")
        funding = {x["symbol"]: safe_float(x.get("lastFundingRate")) for x in premiums if isinstance(x, dict) and "symbol" in x}
        rows = []
        for t in tickers if isinstance(tickers, list) else []:
            sym = t.get("symbol")
            if not sym or not sym.endswith("USDT"):
                continue
            vol = safe_float(t.get("quoteVolume"))
            chg = safe_float(t.get("priceChangePercent"))
            price = safe_float(t.get("lastPrice"))
            rows.append({"symbol": sym, "price": price, "change": chg, "quoteVolume": vol, "funding": funding.get(sym, 0.0)})
        active = [r for r in rows if r["quoteVolume"] >= 10_000_000]
        gainers = sorted(active, key=lambda r: r["change"], reverse=True)[:12]
        losers = sorted(active, key=lambda r: r["change"])[:12]
        volume = sorted(active, key=lambda r: r["quoteVolume"], reverse=True)[:12]
        neg_funding = sorted(active, key=lambda r: r["funding"])[:12]
        pos_funding = sorted(active, key=lambda r: r["funding"], reverse=True)[:12]
        return {
            "symbol_count": len(rows),
            "active_count_10m_volume": len(active),
            "advancers": sum(1 for r in active if r["change"] > 0),
            "decliners": sum(1 for r in active if r["change"] < 0),
            "top_gainers": gainers,
            "top_losers": losers,
            "top_volume": volume,
            "most_negative_funding": neg_funding,
            "most_positive_funding": pos_funding,
        }

    def symbol_microstructure(self, symbol: str) -> dict[str, Any]:
        """Order book, OI, long/short, taker flow, and recent trades for a focused symbol."""
        data: dict[str, Any] = {"symbol": symbol}
        try:
            prem = self.public_api("/fapi/v1/premiumIndex", {"symbol": symbol})
            data["premium"] = {
                "markPrice": safe_float(prem.get("markPrice")),
                "indexPrice": safe_float(prem.get("indexPrice")),
                "lastFundingRate": safe_float(prem.get("lastFundingRate")),
                "nextFundingTime": prem.get("nextFundingTime"),
            }
        except Exception as e:
            data["premium_error"] = str(e)
        try:
            oi = self.public_api("/fapi/v1/openInterest", {"symbol": symbol})
            data["open_interest"] = {"openInterest": safe_float(oi.get("openInterest")), "time": oi.get("time")}
        except Exception as e:
            data["open_interest_error"] = str(e)
        try:
            depth = self.public_api("/fapi/v1/depth", {"symbol": symbol, "limit": 20})
            bids = [(safe_float(p), safe_float(q)) for p, q in depth.get("bids", [])]
            asks = [(safe_float(p), safe_float(q)) for p, q in depth.get("asks", [])]
            bid_notional = sum(p * q for p, q in bids[:10])
            ask_notional = sum(p * q for p, q in asks[:10])
            best_bid = bids[0][0] if bids else 0
            best_ask = asks[0][0] if asks else 0
            data["depth"] = {
                "best_bid": best_bid,
                "best_ask": best_ask,
                "spread_pct": ((best_ask - best_bid) / best_bid * 100) if best_bid else None,
                "bid_notional_10": round(bid_notional, 2),
                "ask_notional_10": round(ask_notional, 2),
                "imbalance_10": round((bid_notional - ask_notional) / max(1e-9, bid_notional + ask_notional), 4),
            }
        except Exception as e:
            data["depth_error"] = str(e)
        try:
            trades = self.public_api("/fapi/v1/aggTrades", {"symbol": symbol, "limit": 120})
            buy_qty = sum(safe_float(t.get("q")) for t in trades if not t.get("m"))
            sell_qty = sum(safe_float(t.get("q")) for t in trades if t.get("m"))
            data["recent_flow"] = {
                "taker_buy_qty": round(buy_qty, 4),
                "taker_sell_qty": round(sell_qty, 4),
                "buy_ratio": round(buy_qty / max(1e-9, buy_qty + sell_qty), 4),
            }
        except Exception as e:
            data["recent_flow_error"] = str(e)
        for key, endpoint in [
            ("global_long_short", "/futures/data/globalLongShortAccountRatio"),
            ("top_account_long_short", "/futures/data/topLongShortAccountRatio"),
            ("top_position_long_short", "/futures/data/topLongShortPositionRatio"),
            ("taker_long_short", "/futures/data/takerlongshortRatio"),
            ("open_interest_hist", "/futures/data/openInterestHist"),
        ]:
            try:
                data[key] = self.public_api(endpoint, {"symbol": symbol, "period": "5m", "limit": 6})
            except Exception as e:
                data[f"{key}_error"] = str(e)
        return data

    def scan_candidates(self, max_klines: int = 50) -> list[dict[str, Any]]:
        if not self.filters:
            self.refresh_filters()
        tickers = self.public_api("/fapi/v1/ticker/24hr")
        premiums = self.public_api("/fapi/v1/premiumIndex")
        funding = {x["symbol"]: safe_float(x.get("lastFundingRate")) for x in premiums if isinstance(x, dict) and "symbol" in x}
        preliminary = []
        for t in tickers:
            sym = t.get("symbol")
            if not sym or sym not in self.filters or not sym.endswith("USDT"):
                continue
            if sym in {"USDCUSDT", "XAUUSDT", "XAGUSDT", "NATGASUSDT", "CLUSDT"}:
                continue
            price = safe_float(t.get("lastPrice"))
            chg = safe_float(t.get("priceChangePercent"))
            vol = safe_float(t.get("quoteVolume"))
            # Broad universe: include majors, high-price coins, quiet pullbacks, and early movers.
            if 0.000001 <= price <= 5000 and vol >= 10_000_000 and -25 <= chg <= 60:
                preliminary.append({"symbol": sym, "price": price, "change": chg, "volume": vol, "funding": funding.get(sym, 0)})
        preliminary.sort(key=lambda x: (abs(x["change"]) + x["volume"] / 50_000_000), reverse=True)
        out = []
        for c in preliminary[:max_klines]:
            sym = c["symbol"]
            try:
                ind = self.indicators(sym)
                i15 = ind.get("15m", {})
                i5 = ind.get("5m", {})
                score = (
                    c["volume"] / 50_000_000
                    + max(0, c["change"]) / 5
                    + (2 if c["funding"] < 0 else 0)
                    + (1 if i15.get("trend") == "up" else 0)
                    + (1 if i5.get("trend") == "up" else 0)
                    - max(0, safe_float(i15.get("rsi14")) - 70) / 10
                )
                out.append({**c, "rsi15": i15.get("rsi14"), "trend5": i5.get("trend"), "trend15": i15.get("trend"), "score": round(score, 3)})
            except Exception:
                continue
        out.sort(key=lambda x: x["score"], reverse=True)
        return out[:15]


# ----------------------------- news collector -----------------------------


class NewsCollector:
    def __init__(self, ttl_seconds: int = 180, env: Optional[dict[str, str]] = None):
        self.ttl_seconds = ttl_seconds
        self.env = env or {}
        self.last_fetch = 0.0
        self.cache: list[dict[str, Any]] = []

    def fetch(self) -> list[dict[str, Any]]:
        if time.time() - self.last_fetch < self.ttl_seconds:
            return self.cache
        items: list[dict[str, Any]] = []
        for source, url in RSS_FEEDS:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "financePi/1.0"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    data = r.read()
                root = ET.fromstring(data)
                for item in root.findall(".//item")[:12]:
                    title = (item.findtext("title") or "").strip()
                    link = (item.findtext("link") or "").strip()
                    pub = (item.findtext("pubDate") or item.findtext("published") or "").strip()
                    desc = re.sub("<[^>]+>", " ", item.findtext("description") or "")
                    text = re.sub(r"\s+", " ", f"{title} {desc}").strip()
                    if title:
                        items.append({"source": source, "title": title, "published": pub, "link": link, "text": text[:500]})
            except Exception as e:
                items.append({"source": source, "error": str(e)})

        # Binance announcements: listings, futures launches, delistings, maintenance.
        # This is Binance's public web CMS endpoint, not the signed trading API.
        try:
            req = urllib.request.Request(
                "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&pageNo=1&pageSize=30",
                headers={"User-Agent": "Mozilla/5.0"},
            )
            with urllib.request.urlopen(req, timeout=20) as r:
                payload = json.loads(r.read().decode())
            for cat in (payload.get("data") or {}).get("catalogs", [])[:8]:
                catalog = cat.get("catalogName", "Binance Announcements")
                for a in cat.get("articles", [])[:8]:
                    title = (a.get("title") or "").strip()
                    code = a.get("code", "")
                    if title:
                        items.append({
                            "source": f"binance:{catalog}",
                            "title": title,
                            "published": datetime.fromtimestamp((a.get("releaseDate") or 0) / 1000).isoformat(timespec="seconds") if a.get("releaseDate") else "",
                            "link": f"https://www.binance.com/en/support/announcement/{code}" if code else "https://www.binance.com/en/support/announcement",
                            "text": title,
                        })
        except Exception as e:
            items.append({"source": "binance_announcements", "error": str(e)})

        # NewsAPI: broader mainstream + crypto coverage if key is configured.
        newsapi_key = self.env.get("NEWSAPI_API_KEY", "").strip()
        if newsapi_key:
            try:
                q = '(crypto OR bitcoin OR ethereum OR Binance OR stablecoin OR ETF OR "Federal Reserve" OR rates)'
                params = urllib.parse.urlencode({
                    "q": q,
                    "language": "en",
                    "sortBy": "publishedAt",
                    "pageSize": 25,
                    "apiKey": newsapi_key,
                })
                with urllib.request.urlopen(f"https://newsapi.org/v2/everything?{params}", timeout=20) as r:
                    payload = json.loads(r.read().decode())
                for a in payload.get("articles", [])[:25]:
                    title = (a.get("title") or "").strip()
                    if title:
                        items.append({
                            "source": f"newsapi:{(a.get('source') or {}).get('name', 'unknown')}",
                            "title": title,
                            "published": a.get("publishedAt", ""),
                            "link": a.get("url", ""),
                            "text": re.sub(r"\s+", " ", f"{title} {a.get('description') or ''}")[:600],
                        })
            except Exception as e:
                items.append({"source": "newsapi", "error": str(e)})

        # Tavily: web search for current crypto/macro context if key is configured.
        tavily_key = self.env.get("TAVILY_API_KEY", "").strip()
        if tavily_key:
            try:
                body = json.dumps({
                    "api_key": tavily_key,
                    "query": "latest cryptocurrency market news bitcoin ethereum altcoins Binance macro Federal Reserve today",
                    "search_depth": "basic",
                    "max_results": 10,
                    "include_answer": False,
                }).encode()
                req = urllib.request.Request(
                    "https://api.tavily.com/search",
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=20) as r:
                    payload = json.loads(r.read().decode())
                for a in payload.get("results", [])[:10]:
                    title = (a.get("title") or "").strip()
                    if title:
                        items.append({
                            "source": "tavily",
                            "title": title,
                            "published": a.get("published_date", ""),
                            "link": a.get("url", ""),
                            "text": re.sub(r"\s+", " ", f"{title} {a.get('content') or ''}")[:700],
                        })
            except Exception as e:
                items.append({"source": "tavily", "error": str(e)})

        # De-duplicate by title/link.
        seen = set()
        deduped = []
        for item in items:
            key = (item.get("title"), item.get("link"))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        self.cache = deduped[:100]
        self.last_fetch = time.time()
        return self.cache

    def macro_context(self) -> list[dict[str, Any]]:
        """Fetch compact FRED macro context if FRED_API_KEY is configured."""
        key = self.env.get("FRED_API_KEY", "").strip()
        if not key:
            return []
        out = []
        for series_id, name in FRED_SERIES.items():
            try:
                params = urllib.parse.urlencode({
                    "series_id": series_id,
                    "api_key": key,
                    "file_type": "json",
                    "sort_order": "desc",
                    "limit": 2,
                })
                with urllib.request.urlopen(f"https://api.stlouisfed.org/fred/series/observations?{params}", timeout=15) as r:
                    payload = json.loads(r.read().decode())
                obs = [o for o in payload.get("observations", []) if o.get("value") not in {None, "."}]
                latest = obs[0] if obs else {}
                prev = obs[1] if len(obs) > 1 else {}
                val = safe_float(latest.get("value"), None)
                prev_val = safe_float(prev.get("value"), None)
                out.append({
                    "series": series_id,
                    "name": name,
                    "date": latest.get("date"),
                    "value": val,
                    "prev_value": prev_val,
                    "delta": round(val - prev_val, 4) if isinstance(val, float) and isinstance(prev_val, float) else None,
                })
            except Exception as e:
                out.append({"series": series_id, "name": name, "error": str(e)})
        return out

    def relevant(self, symbols: list[str], limit: int = 12) -> list[dict[str, Any]]:
        items = self.fetch()
        keywords = ["crypto", "bitcoin", "ethereum", "binance", "fed", "rate", "tariff", "sec", "etf", "stablecoin"]
        for s in symbols:
            keywords.extend(SYMBOL_KEYWORDS.get(s, [s.replace("USDT", "").lower()]))
        out = []
        for item in items:
            text = f"{item.get('title','')} {item.get('text','')}".lower()
            score = sum(1 for k in keywords if k.lower() in text)
            if score > 0 or "error" in item:
                out.append({k: item.get(k) for k in ["source", "title", "published", "link", "error"] if k in item} | {"score": score})
        out.sort(key=lambda x: x.get("score", 0), reverse=True)
        return out[:limit]


# ----------------------------- Pi RPC client -----------------------------


class PiRpcClient:
    def __init__(self, log_path: Path, model: str = "", thinking: str = "low", persistent: bool = True):
        self.log_path = log_path
        session_dir = STATE_DIR / "pi_sessions"
        cmd = ["pi", "--mode", "rpc", "--no-tools", "--thinking", thinking]
        if persistent:
            session_dir.mkdir(parents=True, exist_ok=True)
            cmd += ["-c", "--session-dir", str(session_dir)]
        else:
            cmd += ["--no-session"]
        if model:
            cmd += ["--model", model]
        self.proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self.q: queue.Queue[dict[str, Any]] = queue.Queue()
        self.stderr_q: queue.Queue[str] = queue.Queue()
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        self.req_id = 0
        log_json(self.log_path, "pi_rpc_started", pid=self.proc.pid, cmd=" ".join(cmd), persistent=persistent)

    def _read_stdout(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            line = line.rstrip("\n").rstrip("\r")
            if not line:
                continue
            try:
                self.q.put(json.loads(line))
            except Exception:
                self.q.put({"type": "raw", "line": line})

    def _read_stderr(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            self.stderr_q.put(line.rstrip("\n"))

    def send(self, obj: dict[str, Any]) -> None:
        if self.proc.poll() is not None:
            raise RuntimeError(f"pi rpc exited with {self.proc.returncode}")
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def request(self, obj: dict[str, Any], timeout: int = 20) -> dict[str, Any]:
        self.req_id += 1
        req_id = f"rpc-{self.req_id}"
        obj = {**obj, "id": req_id}
        self.send(obj)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                ev = self.q.get(timeout=1)
            except queue.Empty:
                if self.proc.poll() is not None:
                    raise RuntimeError(f"pi rpc exited with {self.proc.returncode}")
                continue
            if ev.get("type") == "response" and ev.get("id") == req_id:
                return ev
            log_json(self.log_path, "pi_rpc_startup_event", event_payload=ev)
        raise TimeoutError(f"Pi RPC request timed out: {obj.get('type')}")

    def prompt(self, message: str, timeout: int = 240) -> str:
        self.req_id += 1
        req_id = f"prompt-{self.req_id}"
        self.send({"id": req_id, "type": "prompt", "message": message})
        accepted = False
        chunks: list[str] = []
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                ev = self.q.get(timeout=1)
            except queue.Empty:
                if self.proc.poll() is not None:
                    raise RuntimeError(f"pi rpc exited with {self.proc.returncode}")
                continue
            et = ev.get("type")
            if et == "response" and ev.get("id") == req_id:
                if not ev.get("success"):
                    raise RuntimeError(f"pi prompt rejected: {ev}")
                accepted = True
            elif et == "message_update":
                delta = ev.get("assistantMessageEvent", {})
                if delta.get("type") == "text_delta":
                    chunks.append(delta.get("delta", ""))
            elif et == "agent_end" and accepted:
                return "".join(chunks).strip()
            elif et in {"extension_ui_request", "raw"}:
                log_json(self.log_path, "pi_rpc_event", event_payload=ev)
        raise TimeoutError("Pi RPC prompt timed out")

    def close(self) -> None:
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass


# ----------------------------- supervisor -----------------------------


@dataclass
class RiskConfig:
    max_single_loss_frac: float = 0.10
    max_leverage: int = 10
    max_notional_equity_mult: float = 7.5
    min_order_notional_usdt: float = 5.5
    min_confidence_open: float = 0.64
    min_confidence_reduce: float = 0.45
    max_positions: int = 2


class TradingSupervisor:
    def __init__(self, live: bool, interval: int, pi_interval: int, review_interval: int, model: str, thinking: str, persistent_pi_session: bool):
        self.live = live
        self.interval = interval
        self.pi_interval = pi_interval
        self.review_interval = review_interval
        self.log_path = LOG_DIR / f"pi_supervisor_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
        self.state_path = STATE_DIR / "pi_supervisor_state.json"
        env = {**load_dotenv(ROOT / ".env"), **os.environ}
        api_key = env.get("BINANCE_API_KEY", "").strip()
        key_path = Path(env.get("BINANCE_PRIVATE_KEY_PATH", "keys/binance_private.pem").strip())
        if not key_path.is_absolute():
            key_path = ROOT / key_path
        if not api_key or not key_path.exists():
            raise RuntimeError("Missing BINANCE_API_KEY or BINANCE_PRIVATE_KEY_PATH")
        self.binance = BinanceClient(api_key, key_path, self.log_path)
        self.news = NewsCollector(env=env)
        self.pi = PiRpcClient(self.log_path, model=model, thinking=thinking, persistent=persistent_pi_session)
        try:
            state = self.pi.request({"type": "get_state"})
            log_json(self.log_path, "pi_state", state=state.get("data"))
            self.pi.request({"type": "set_session_name", "name": "CryptoPilot trading supervisor"})
        except Exception as e:
            log_json(self.log_path, "pi_state_error", error=str(e))
        self.risk = RiskConfig()
        self.stop_event = threading.Event()
        self.last_pi_call = 0.0
        self.last_review_call = 0.0
        self.binance.refresh_filters()

    def build_snapshot(self) -> dict[str, Any]:
        account = self.binance.account_state()
        positions = account["positions"]
        symbols = [p["symbol"] for p in positions]
        market_overview = self.binance.market_overview()
        candidates = self.binance.scan_candidates()
        # Blend quantitative scan with all-market leaders so Pi sees more than one narrow shortlist.
        overview_symbols = []
        for bucket in ["top_volume", "top_gainers", "top_losers", "most_negative_funding", "most_positive_funding"]:
            overview_symbols.extend([x.get("symbol") for x in market_overview.get(bucket, [])[:5]])
        candidate_symbols = [c["symbol"] for c in candidates[:12]]
        focus_symbols = [s for s in dict.fromkeys(symbols + candidate_symbols + overview_symbols) if s][:16]
        indicators = {s: self.binance.indicators(s) for s in focus_symbols}
        microstructure = {s: self.binance.symbol_microstructure(s) for s in focus_symbols[:10]}
        open_orders = {s: self.binance.open_orders(s) for s in symbols}
        algo_orders = {s: self.binance.open_algo_orders(s) for s in symbols}
        recent_income = self.binance.recent_income(limit=60)
        recent_trades = {s: self.binance.recent_trades(s, limit=16) for s in focus_symbols[:8]}
        news = self.news.relevant(focus_symbols, limit=20)
        macro = self.news.macro_context()
        snapshot = {
            "time": now_iso(),
            "mode": "LIVE" if self.live else "DRY_RUN",
            "account": account,
            "positions": positions,
            "open_orders": open_orders,
            "open_algo_orders": algo_orders,
            "recent_income": recent_income,
            "recent_trades": recent_trades,
            "market_overview_all_symbols": market_overview,
            "market_indicators": indicators,
            "market_microstructure": microstructure,
            "candidates": candidates,
            "news": news,
            "macro_context_fred": macro,
            "risk_policy": {
                "max_positions": self.risk.max_positions,
                "max_single_loss_frac": self.risk.max_single_loss_frac,
                "max_leverage": self.risk.max_leverage,
                "max_notional_equity_mult": self.risk.max_notional_equity_mult,
                "min_order_notional_usdt": self.risk.min_order_notional_usdt,
                "ai_is_primary_decision_maker": True,
                "execution_requires_python_risk_gate": True,
                "must_have_stop_loss": True,
            },
        }
        return snapshot

    def make_prompt(self, snapshot: dict[str, Any]) -> str:
        return f"""
你是 CryptoPilot 的 Pi 决策脑，但你没有交易所权限。Python 风控执行器会审查你的 JSON。

目标：持续盯盘、结合实时新闻/行情/仓位/前文复盘记忆，给出你认为期望值最高的交易或调仓决策。
你运行在同一个持久 Pi 会话中：请主动利用之前的错误、有效规则、已做过的判断，动态调整策略。

决策要求：
1. 只输出一个 JSON 对象，不要 Markdown，不要解释。
2. 你是主决策者：市场持续波动时，必须主动寻找至少一个最优交易方向；除非全市场明显无边际优势，否则不要 hold；优先给出可用 post-only 限价挂单方案。
3. 若已有持仓，主动判断：close、reduce、tighten_stop、move_stop_to_breakeven、hold，或是否允许再开第二个不冲突仓位。
4. 开新仓需要给出明确 stop_loss、take_profit、invalid_if；confidence >= 0.64 即可提交提案。
5. 可以基于技术面、资金费率、新闻催化、Binance公告、FRED宏观数据、BTC/ETH 联动和盘口波动主动做多或做空。
6. 每轮必须横向比较候选列表、全市场涨跌幅/成交量/资金费率榜、订单簿/成交流/OI/多空比、新闻/情绪/事件/宏观；如果 hold，需要说明为什么最佳替代币也不值得做。
7. Binance 下单名义金额必须留余量，notional_usdt 不要低于 5.5。
8. 默认执行会先用 post-only maker 限价单挂 35 秒，未成交就取消，不追价；所以提案应给出不怕错过的高质量入场，不要依赖市价追单。
9. 当前系统过去亏损多来自过度短线和手续费；除非 thesis 明确失效，不要只因 1m 噪音要求 close。

可选 decision：
- hold
- close
- reduce
- tighten_stop
- move_stop_to_breakeven
- open_long
- open_short

JSON 格式：
{{
  "decision": "hold|close|reduce|tighten_stop|move_stop_to_breakeven|open_long|open_short",
  "symbol": "SEIUSDT 或 null",
  "confidence": 0.0,
  "reason": "一句话核心理由",
  "risk_notes": ["..."],
  "proposal": {{
    "notional_usdt": null,
    "leverage": null,
    "stop_loss": null,
    "take_profit": null,
    "reduce_fraction": null,
    "new_stop": null,
    "invalid_if": "..."
  }}
}}

当前快照：
{json.dumps(snapshot, ensure_ascii=False, indent=2)[:45000]}
""".strip()

    def ask_pi(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        prompt = self.make_prompt(snapshot)
        text = self.pi.prompt(prompt)
        log_json(self.log_path, "pi_raw_response", text=text)
        decision = extract_json_object(text)
        log_json(self.log_path, "pi_decision", decision=decision)
        return decision

    def make_review_prompt(self, snapshot: dict[str, Any], decision: Optional[dict[str, Any]], risk_result: Optional[str]) -> str:
        review_pack = {
            "time": snapshot.get("time"),
            "mode": snapshot.get("mode"),
            "account": snapshot.get("account"),
            "positions": snapshot.get("positions"),
            "recent_income": snapshot.get("recent_income"),
            "recent_trades": snapshot.get("recent_trades"),
            "market_overview_all_symbols": snapshot.get("market_overview_all_symbols"),
            "market_indicators": snapshot.get("market_indicators"),
            "market_microstructure": snapshot.get("market_microstructure"),
            "candidates": snapshot.get("candidates")[:8],
            "news": snapshot.get("news")[:12],
            "macro_context_fred": snapshot.get("macro_context_fred"),
            "last_decision": decision,
            "last_risk_result": risk_result,
        }
        return f"""
这是同一个 CryptoPilot 持久会话中的周期性复盘。请利用前文上下文，总结最近交易/持仓/机会判断中的经验，并形成下一轮盯盘注意事项。

要求：
1. 只输出 JSON 对象，不要 Markdown。
2. 复盘是为了改进后续决策，不直接执行交易。
3. 复盘要服务于后续交易质量：提炼可执行规则、指出应该更主动还是更收敛。
4. 如果发现当前持仓风险或机会明显，请在 action_hint 中直接指出 close/reduce/tighten_stop/move_stop_to_breakeven/look_for_new_entry。

JSON 格式：
{{
  "review_type": "periodic|post_decision|post_trade",
  "summary": "一句话复盘",
  "what_worked": ["..."],
  "mistakes_or_risks": ["..."],
  "memory_rules": ["后续必须记住的规则"],
  "next_watch": ["接下来重点盯什么"],
  "action_hint": "hold|close|reduce|tighten_stop|move_stop_to_breakeven|look_for_new_entry",
  "confidence": 0.0
}}

复盘快照：
{json.dumps(review_pack, ensure_ascii=False, indent=2)[:42000]}
""".strip()

    def review_with_pi(self, snapshot: dict[str, Any], decision: Optional[dict[str, Any]], risk_result: Optional[str], force: bool = False) -> Optional[dict[str, Any]]:
        if not force and time.time() - self.last_review_call < self.review_interval:
            return None
        self.last_review_call = time.time()
        text = self.pi.prompt(self.make_review_prompt(snapshot, decision, risk_result), timeout=240)
        log_json(self.log_path, "pi_review_raw", text=text)
        review = extract_json_object(text)
        log_json(self.log_path, "pi_review", review=review)
        return review

    def risk_check(self, decision: dict[str, Any], snapshot: dict[str, Any]) -> tuple[bool, str]:
        allowed = {"hold", "close", "reduce", "tighten_stop", "move_stop_to_breakeven", "open_long", "open_short"}
        d = decision.get("decision")
        if d not in allowed:
            return False, f"invalid decision {d}"
        if d == "hold":
            return True, "hold"
        symbol = decision.get("symbol")
        if not symbol or symbol not in self.binance.filters:
            return False, "missing/invalid symbol"
        account = snapshot["account"]
        wallet = safe_float(account.get("wallet"))
        positions = account.get("positions", [])
        pos = next((p for p in positions if p.get("symbol") == symbol), None)
        proposal = decision.get("proposal") or {}
        confidence = safe_float(decision.get("confidence"))

        if d in {"close", "reduce", "tighten_stop", "move_stop_to_breakeven"}:
            if not pos:
                return False, "no position to adjust"
            if d == "reduce" and confidence < self.risk.min_confidence_reduce:
                return False, "reduce confidence too low"
            if d == "reduce":
                frac = safe_float(proposal.get("reduce_fraction"))
                if not (0.1 <= frac <= 1.0):
                    return False, "bad reduce_fraction"
            if d == "tighten_stop":
                new_stop = safe_float(proposal.get("new_stop"))
                entry = safe_float(pos.get("entryPrice"))
                amt = safe_float(pos.get("positionAmt"))
                mark = safe_float(pos.get("markPrice"))
                if new_stop <= 0:
                    return False, "missing new_stop"
                if amt > 0 and not (entry * 0.98 <= new_stop < mark):
                    return False, "long new_stop out of range"
                if amt < 0 and not (mark < new_stop <= entry * 1.02):
                    return False, "short new_stop out of range"
            return True, "position adjustment ok"

        # Opening new position
        if len(positions) >= self.risk.max_positions:
            return False, "max positions reached"
        if confidence < self.risk.min_confidence_open:
            return False, "open confidence too low"
        notional = safe_float(proposal.get("notional_usdt"))
        leverage = int(safe_float(proposal.get("leverage")))
        stop_loss = safe_float(proposal.get("stop_loss"))
        take_profit = safe_float(proposal.get("take_profit"))
        if notional <= 0 or leverage <= 0 or stop_loss <= 0 or take_profit <= 0:
            return False, "open missing notional/leverage/stop/tp"
        notional = max(notional, self.risk.min_order_notional_usdt)
        if leverage > self.risk.max_leverage:
            return False, "leverage too high"
        if notional > wallet * self.risk.max_notional_equity_mult:
            return False, "notional too high"
        ind = snapshot.get("market_indicators", {}).get(symbol, {})
        price = safe_float((ind.get("1m") or {}).get("last")) or safe_float((ind.get("5m") or {}).get("last"))
        if price <= 0:
            return False, "missing current price"
        if d == "open_long":
            if not (stop_loss < price < take_profit):
                return False, "bad long stop/tp geometry"
            risk_usdt = (price - stop_loss) / price * notional
            reward_usdt = (take_profit - price) / price * notional
        else:
            if not (take_profit < price < stop_loss):
                return False, "bad short stop/tp geometry"
            risk_usdt = (stop_loss - price) / price * notional
            reward_usdt = (price - take_profit) / price * notional
        if risk_usdt > wallet * self.risk.max_single_loss_frac:
            return False, f"risk too high {risk_usdt:.4f}"
        if reward_usdt < risk_usdt * 1.05:
            return False, "RR too low"
        return True, "open ok"

    def execute(self, decision: dict[str, Any], snapshot: dict[str, Any], approval_reason: str) -> None:
        d = decision["decision"]
        symbol = decision.get("symbol")
        proposal = decision.get("proposal") or {}
        if d == "hold":
            log_json(self.log_path, "execution_hold", reason=decision.get("reason"))
            return
        if not self.live:
            log_json(self.log_path, "execution_dry_run", decision=decision, approval_reason=approval_reason)
            return

        account = snapshot["account"]
        pos = next((p for p in account.get("positions", []) if p.get("symbol") == symbol), None)
        if d == "close" and pos:
            res = self.binance.close_position_market(symbol, safe_float(pos.get("positionAmt")))
            self.binance.cancel_all_orders(symbol)
            self.binance.cancel_open_algo_orders(symbol)
            log_json(self.log_path, "executed_close", symbol=symbol, result=res)
            return
        if d == "reduce" and pos:
            frac = min(1.0, max(0.1, safe_float(proposal.get("reduce_fraction"))))
            qty = safe_float(pos.get("positionAmt")) * frac
            res = self.binance.close_position_market(symbol, qty)
            log_json(self.log_path, "executed_reduce", symbol=symbol, fraction=frac, result=res)
            return
        if d in {"tighten_stop", "move_stop_to_breakeven"} and pos:
            amt = safe_float(pos.get("positionAmt"))
            side = "SELL" if amt > 0 else "BUY"
            if d == "move_stop_to_breakeven":
                entry = safe_float(pos.get("entryPrice"))
                stop = entry * (1.001 if amt > 0 else 0.999)
            else:
                stop = safe_float(proposal.get("new_stop"))
            res = self.binance.place_hard_stop(symbol, side, stop)
            log_json(self.log_path, "executed_stop_update", symbol=symbol, stop=stop, result=res)
            return
        if d in {"open_long", "open_short"}:
            leverage = int(safe_float(proposal.get("leverage")))
            notional = max(safe_float(proposal.get("notional_usdt")), self.risk.min_order_notional_usdt)
            price = safe_float((snapshot.get("market_indicators", {}).get(symbol, {}).get("1m") or {}).get("last"))
            qty = notional / price
            self.binance.set_leverage(symbol, leverage)
            side = "BUY" if d == "open_long" else "SELL"
            res = self.binance.place_limit_entry_with_wait(symbol, side, qty, wait_seconds=35)
            executed_qty = safe_float(res.get("executedQty")) if isinstance(res, dict) else 0.0
            # Some responses are wrappers after cancel; inspect final/last for fills.
            if isinstance(res, dict) and executed_qty <= 0:
                for key in ("final", "last", "initial"):
                    if isinstance(res.get(key), dict):
                        executed_qty = max(executed_qty, safe_float(res[key].get("executedQty")))
            if executed_qty <= 0:
                log_json(self.log_path, "entry_not_filled", symbol=symbol, side=side, result=res)
                return
            stop_side = "SELL" if d == "open_long" else "BUY"
            stop_res = self.binance.place_hard_stop(symbol, stop_side, safe_float(proposal.get("stop_loss")))
            log_json(self.log_path, "executed_open", symbol=symbol, side=side, execution="maker_limit", result=res, stop_result=stop_res)
            return

    def save_state(self, snapshot: dict[str, Any], decision: Optional[dict[str, Any]] = None) -> None:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"updated_at": now_iso(), "snapshot": snapshot, "last_decision": decision}, ensure_ascii=False, indent=2))
        tmp.replace(self.state_path)

    def cycle(self, force_pi: bool = False) -> None:
        snapshot = self.build_snapshot()
        self.save_state(snapshot)
        log_json(
            self.log_path,
            "snapshot",
            wallet=snapshot["account"].get("wallet"),
            available=snapshot["account"].get("available"),
            positions=[{"symbol": p.get("symbol"), "amt": p.get("positionAmt"), "pnl": p.get("unrealizedProfit")} for p in snapshot["positions"]],
            candidates=snapshot["candidates"][:3],
            news=snapshot["news"][:3],
        )
        # Ask Pi more often when there is a position; otherwise throttle opportunity scans.
        has_position = bool(snapshot["positions"])
        due = force_pi or has_position or (time.time() - self.last_pi_call >= self.pi_interval)
        if not due:
            return
        self.last_pi_call = time.time()
        decision = self.ask_pi(snapshot)
        ok, reason = self.risk_check(decision, snapshot)
        log_json(self.log_path, "risk_check", approved=ok, reason=reason, decision=decision)
        self.save_state(snapshot, decision)
        if ok:
            self.execute(decision, snapshot, reason)
        # Keep the same Pi conversation warm with frequent reviews. Force a review after
        # any non-hold proposal so the lesson is immediately added to memory.
        self.review_with_pi(snapshot, decision, reason, force=(decision.get("decision") != "hold"))

    def run(self) -> None:
        log_json(self.log_path, "supervisor_start", live=self.live, interval=self.interval, pi_interval=self.pi_interval, review_interval=self.review_interval)

        def _stop(signum, frame):
            self.stop_event.set()
            log_json(self.log_path, "signal", signum=signum)

        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)
        try:
            self.cycle(force_pi=True)
            while not self.stop_event.is_set():
                time.sleep(self.interval)
                try:
                    self.cycle()
                except Exception as e:
                    log_json(self.log_path, "cycle_error", error=str(e), traceback=traceback.format_exc())
        finally:
            self.pi.close()
            log_json(self.log_path, "supervisor_stop")


# ----------------------------- main -----------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description="Pi-driven trading supervisor")
    ap.add_argument("--live", action="store_true", help="Allow execution. Default is dry-run.")
    ap.add_argument("--interval", type=int, default=60, help="Snapshot/position loop interval seconds")
    ap.add_argument("--pi-interval", type=int, default=120, help="Pi opportunity decision interval when flat")
    ap.add_argument("--review-interval", type=int, default=900, help="Periodic Pi review interval seconds")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="Pi model pattern; default openai-codex/gpt-5.5 unless PI_TRADING_MODEL overrides")
    ap.add_argument("--thinking", default="low", choices=["off", "minimal", "low", "medium", "high", "xhigh"])
    ap.add_argument("--ephemeral-pi-session", action="store_true", help="Do not continue the persistent Pi trading conversation")
    args = ap.parse_args()
    sup = TradingSupervisor(
        live=args.live,
        interval=args.interval,
        pi_interval=args.pi_interval,
        review_interval=args.review_interval,
        model=args.model,
        thinking=args.thinking,
        persistent_pi_session=not args.ephemeral_pi_session,
    )
    sup.run()


if __name__ == "__main__":
    main()
