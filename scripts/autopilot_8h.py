#!/usr/bin/env python3
"""
8h USDT-M futures autopilot with strict risk gates.

目标：尝试在 8 小时内通过短线多单累计收益。
风控：初始止损由本脚本主动监控触发；触发后 reduceOnly 市价平仓。
"""

import base64
import json
import math
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from urllib.parse import urlencode

from cryptography.hazmat.primitives import serialization

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_PATH = os.path.join(ROOT, "logs", f"autopilot_8h_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl")


def load_dotenv(path: str) -> dict[str, str]:
    env: dict[str, str] = {}
    if not os.path.exists(path):
        return env
    with open(path) as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


_env = {**load_dotenv(os.path.join(ROOT, ".env")), **os.environ}
API_KEY = _env.get("BINANCE_API_KEY", "").strip()
PRIVATE_KEY_PATH = _env.get(
    "BINANCE_PRIVATE_KEY_PATH",
    os.path.join(ROOT, "keys", "binance_private.pem"),
).strip()
if not API_KEY or not os.path.exists(PRIVATE_KEY_PATH):
    raise RuntimeError("Missing BINANCE_API_KEY or BINANCE_PRIVATE_KEY_PATH")

RUN_SECONDS = 8 * 60 * 60
SCAN_INTERVAL = 30
POSITION_POLL_INTERVAL = 5
LEVERAGE = 10
NOTIONAL_EQUITY_MULT = 7.5
MAX_MARGIN_EQUITY_FRAC = 0.85
MAX_SINGLE_LOSS_EQUITY_FRAC = 0.10
STOP_PCT = 0.013       # 1.3% price move; capped so single-trade risk <= 10% equity
TP1_PCT = 0.025        # price-based first take-profit fallback
TP2_PCT = 0.050        # close remaining
TP1_FRAC = 0.50
PROTECT_PROFIT_USDT = 1.0      # DRIFT review: do not let +1U profit turn into loss
FORCE_TP1_PROFIT_USDT = 1.5    # DRIFT review: lock partial profit around +1.5U
BREAKEVEN_BUFFER_PCT = 0.001
MAX_SESSION_DRAWDOWN = 0.16    # roughly two max-risk losses, then stop
TARGET_SESSION_GAIN = 0.60
MAX_HOLD_SECONDS = 90 * 60
MIN_WALLET_TO_TRADE = 5.0

EXCLUDE = {
    "USDCUSDT", "XAUUSDT", "XAGUSDT", "NATGASUSDT", "CLUSDT",
    "BTCUSDT", "ETHUSDT"  # avoid large caps for this high-return short-window objective
}

with open(PRIVATE_KEY_PATH, "rb") as f:
    PRIVATE_KEY = serialization.load_pem_private_key(f.read(), password=None)


def log(event, **data):
    row = {"ts": datetime.now().isoformat(timespec="seconds"), "event": event, **data}
    print(json.dumps(row, ensure_ascii=False), flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def sign(query: str) -> str:
    return base64.b64encode(PRIVATE_KEY.sign(query.encode())).decode()


def signed_api(endpoint, params=None, method="GET"):
    params = params or {}
    params["timestamp"] = int(time.time() * 1000)
    query = urlencode(params)
    params["signature"] = sign(query)
    url = f"https://fapi.binance.com{endpoint}?{urlencode(params)}"
    cmd = ["curl", "-s", "-X", method, "-H", f"X-MBX-APIKEY: {API_KEY}", url] if method != "GET" else ["curl", "-s", "-H", f"X-MBX-APIKEY: {API_KEY}", url]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout
    try:
        return json.loads(out)
    except Exception:
        return {"raw": out}


def public_api(endpoint):
    out = subprocess.run(["curl", "-s", f"https://fapi.binance.com{endpoint}"], capture_output=True, text=True, timeout=20).stdout
    return json.loads(out)


def account_state():
    acc = signed_api("/fapi/v2/account")
    wallet = float(acc.get("totalWalletBalance", 0))
    available = float(acc.get("availableBalance", 0))
    unreal = float(acc.get("totalUnrealizedProfit", 0))
    positions = []
    for p in acc.get("positions", []):
        amt = float(p.get("positionAmt", 0))
        if amt != 0:
            positions.append(p)
    return wallet, available, unreal, positions


def cancel_all_orders(symbol=None):
    if symbol:
        return signed_api("/fapi/v1/allOpenOrders", {"symbol": symbol}, "DELETE")
    orders = signed_api("/fapi/v1/openOrders")
    for o in orders if isinstance(orders, list) else []:
        signed_api("/fapi/v1/order", {"symbol": o["symbol"], "orderId": o["orderId"]}, "DELETE")
    return orders


def cancel_open_algo_orders(symbol=None):
    params = {"symbol": symbol} if symbol else {}
    orders = signed_api("/fapi/v1/openAlgoOrders", params)
    for o in orders if isinstance(orders, list) else []:
        signed_api("/fapi/v1/algoOrder", {"symbol": o["symbol"], "algoId": o["algoId"]}, "DELETE")
    return orders


def get_exchange_filters():
    info = public_api("/fapi/v1/exchangeInfo")
    filters = {}
    for s in info.get("symbols", []):
        if s.get("contractType") != "PERPETUAL" or s.get("quoteAsset") != "USDT" or s.get("status") != "TRADING":
            continue
        lot = next((f for f in s.get("filters", []) if f.get("filterType") == "LOT_SIZE"), {})
        price_filter = next((f for f in s.get("filters", []) if f.get("filterType") == "PRICE_FILTER"), {})
        filters[s["symbol"]] = {
            "stepSize": float(lot.get("stepSize", 1)),
            "minQty": float(lot.get("minQty", 0)),
            "tickSize": float(price_filter.get("tickSize", 0.00000001)),
        }
    return filters


def floor_to_step(value, step):
    if step <= 0:
        return value
    return math.floor(value / step) * step


def fmt_by_step(value, step):
    v = floor_to_step(value, step)
    decimals = max(0, int(round(-math.log10(step)))) if step < 1 else 0
    return f"{v:.{decimals}f}"


def round_price(value, tick):
    if tick <= 0:
        return value
    v = round(value / tick) * tick
    decimals = max(0, int(round(-math.log10(tick)))) if tick < 1 else 0
    return f"{v:.{decimals}f}"


def rsi(closes, period=14):
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


def klines(symbol, interval="15m", limit=30):
    return public_api(f"/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}")


def scan_candidates(filters):
    tickers = public_api("/fapi/v1/ticker/24hr")
    premiums = public_api("/fapi/v1/premiumIndex")
    funding = {x["symbol"]: float(x.get("lastFundingRate", 0)) for x in premiums if "symbol" in x}
    cands = []
    for t in tickers:
        sym = t.get("symbol")
        if sym in EXCLUDE or sym not in filters or not sym.endswith("USDT"):
            continue
        price = float(t.get("lastPrice", 0))
        chg = float(t.get("priceChangePercent", 0))
        vol = float(t.get("quoteVolume", 0))
        fr = funding.get(sym, 0)
        if not (0.01 <= price <= 20 and vol >= 20_000_000 and 2 <= chg <= 30 and fr <= 0.00005):
            continue
        try:
            k15 = klines(sym, "15m", 30)
            closes = [float(k[4]) for k in k15]
            highs = [float(k[2]) for k in k15[-12:]]
            lows = [float(k[3]) for k in k15[-12:]]
            rrsi = rsi(closes)
            pos = (closes[-1] - min(lows)) / max(1e-12, (max(highs) - min(lows)))
            k5 = klines(sym, "5m", 8)
            c5 = [float(k[4]) for k in k5]
            short_momentum = c5[-1] > c5[-2] and c5[-2] >= min(c5[-5:])
        except Exception as e:
            continue
        if not (30 <= rrsi <= 62 and pos <= 0.80 and short_momentum):
            continue
        score = (-fr * 100000) + (vol / 50_000_000) + (30 - abs(chg - 12)) / 10 + (62 - rrsi) / 20
        cands.append({"symbol": sym, "price": price, "change": chg, "volume": vol, "funding": fr, "rsi15": rrsi, "range_pos": pos, "score": score})
    cands.sort(key=lambda x: x["score"], reverse=True)
    return cands


def set_leverage(symbol):
    res = signed_api("/fapi/v1/leverage", {"symbol": symbol, "leverage": LEVERAGE}, "POST")
    log("set_leverage", symbol=symbol, result=res)


def place_market_buy(symbol, price, wallet, filters):
    f = filters[symbol]
    risk_capped_notional = wallet * MAX_SINGLE_LOSS_EQUITY_FRAC / STOP_PCT
    notional = min(wallet * NOTIONAL_EQUITY_MULT, wallet * LEVERAGE * MAX_MARGIN_EQUITY_FRAC, risk_capped_notional)
    qty = notional / price
    qty_s = fmt_by_step(qty, f["stepSize"])
    if float(qty_s) < f["minQty"]:
        log("qty_too_small", symbol=symbol, qty=qty_s, minQty=f["minQty"])
        return None
    set_leverage(symbol)
    res = signed_api("/fapi/v1/order", {"symbol": symbol, "side": "BUY", "type": "MARKET", "quantity": qty_s}, "POST")
    log("entry_order", symbol=symbol, qty=qty_s, estimated_notional=notional, result=res)
    return res


def close_market(symbol, qty, reason, filters):
    f = filters[symbol]
    qty_s = fmt_by_step(abs(qty), f["stepSize"])
    res = signed_api("/fapi/v1/order", {"symbol": symbol, "side": "SELL", "type": "MARKET", "quantity": qty_s, "reduceOnly": "true"}, "POST")
    log("close_market", symbol=symbol, qty=qty_s, reason=reason, result=res)
    return res


def place_hard_stop(symbol, stop_price, filters, reason):
    """Place an exchange-side closePosition STOP_MARKET order.

    This is intentionally separate from the polling stop so a script/network failure
    does not leave a naked leveraged position.
    """
    f = filters[symbol]
    cancel_all_orders(symbol)
    cancel_open_algo_orders(symbol)
    stop_s = round_price(stop_price, f["tickSize"])
    res = signed_api("/fapi/v1/algoOrder", {
        "symbol": symbol,
        "side": "SELL",
        "algoType": "CONDITIONAL",
        "type": "STOP_MARKET",
        "triggerPrice": stop_s,
        "closePosition": "true",
        "workingType": "MARK_PRICE",
    }, "POST")
    log("hard_stop_order", symbol=symbol, stop=stop_s, reason=reason, result=res)
    return res


def manage_position(symbol, start_time, filters):
    entry = None
    qty0 = None
    tp1_done = False
    breakeven_done = False
    stop_price = None
    tp1_price = None
    tp2_price = None
    log("manage_start", symbol=symbol)
    while time.time() - start_time < RUN_SECONDS:
        wallet, available, unreal, positions = account_state()
        pos = next((p for p in positions if p["symbol"] == symbol), None)
        if not pos:
            log("position_closed", symbol=symbol, wallet=wallet, available=available)
            return
        qty = float(pos["positionAmt"])
        entry = float(pos["entryPrice"])
        mark = float(pos.get("markPrice") or public_api(f"/fapi/v1/ticker/price?symbol={symbol}")["price"])
        if qty0 is None:
            qty0 = abs(qty)
            notional = entry * qty0
            risk_cap_stop_pct = min(STOP_PCT, (wallet * MAX_SINGLE_LOSS_EQUITY_FRAC) / max(1e-12, notional))
            stop_price = entry * (1 - risk_cap_stop_pct)
            tp1_price = entry * (1 + TP1_PCT)
            tp2_price = entry * (1 + TP2_PCT)
            log("risk_levels", symbol=symbol, entry=entry, qty=qty0, notional=notional, stop=stop_price, tp1=tp1_price, tp2=tp2_price, max_loss_usdt=wallet * MAX_SINGLE_LOSS_EQUITY_FRAC)
            place_hard_stop(symbol, stop_price, filters, "initial_stop")
        pnl = float(pos["unrealizedProfit"])
        log("position_tick", symbol=symbol, mark=mark, entry=entry, qty=qty, pnl=pnl, wallet=wallet, stop=stop_price)
        if mark <= stop_price:
            close_market(symbol, qty, "polling_stop_loss", filters)
            cancel_all_orders(symbol)
            cancel_open_algo_orders(symbol)
            return
        if (not breakeven_done) and pnl >= PROTECT_PROFIT_USDT:
            stop_price = max(stop_price, entry * (1 + BREAKEVEN_BUFFER_PCT))
            breakeven_done = True
            place_hard_stop(symbol, stop_price, filters, "profit_protect_1u")
            log("move_stop", symbol=symbol, new_stop=stop_price, trigger_pnl=pnl)
        if (not tp1_done) and (mark >= tp1_price or pnl >= FORCE_TP1_PROFIT_USDT):
            close_qty = abs(qty0) * TP1_FRAC
            close_market(symbol, close_qty, "tp1_or_profit_lock", filters)
            tp1_done = True
            stop_price = max(stop_price, entry * (1 + BREAKEVEN_BUFFER_PCT))
            place_hard_stop(symbol, stop_price, filters, "after_tp1_breakeven")
            log("move_stop", symbol=symbol, new_stop=stop_price)
        elif tp1_done and mark >= tp2_price:
            close_market(symbol, qty, "tp2", filters)
            cancel_all_orders(symbol)
            cancel_open_algo_orders(symbol)
            return
        if time.time() - start_time > MAX_HOLD_SECONDS and pnl < wallet * 0.02:
            close_market(symbol, qty, "time_exit", filters)
            cancel_all_orders(symbol)
            cancel_open_algo_orders(symbol)
            return
        time.sleep(POSITION_POLL_INTERVAL)


def main():
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    filters = get_exchange_filters()
    start_ts = time.time()
    start_wallet, available, unreal, positions = account_state()
    log("start", wallet=start_wallet, available=available, positions=len(positions), log_path=LOG_PATH)
    if positions:
        log("resume_existing_position", positions=positions)
        manage_position(positions[0]["symbol"], start_ts, filters)
    cancel_all_orders()
    cancel_open_algo_orders()
    while time.time() - start_ts < RUN_SECONDS:
        wallet, available, unreal, positions = account_state()
        if positions:
            manage_position(positions[0]["symbol"], start_ts, filters)
            continue
        if wallet < MIN_WALLET_TO_TRADE:
            log("stop_low_wallet", wallet=wallet)
            return
        if wallet <= start_wallet * (1 - MAX_SESSION_DRAWDOWN):
            log("stop_session_drawdown", wallet=wallet, start_wallet=start_wallet)
            return
        if wallet >= start_wallet * (1 + TARGET_SESSION_GAIN):
            log("stop_target_reached", wallet=wallet, start_wallet=start_wallet)
            return
        cands = scan_candidates(filters)
        log("scan", wallet=wallet, available=available, candidates=cands[:5])
        if cands and available > wallet * 0.50:
            best = cands[0]
            place_market_buy(best["symbol"], best["price"], wallet, filters)
            time.sleep(5)
            manage_position(best["symbol"], start_ts, filters)
        time.sleep(SCAN_INTERVAL)
    log("finished_time", wallet=account_state()[0])


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("fatal", error=str(e))
        raise
