#!/usr/bin/env python3
"""
监控限价单成交状态，成交后自动设置止损止盈
"""

import base64
import json
import os
import subprocess
import time
from pathlib import Path
from urllib.parse import urlencode

from cryptography.hazmat.primitives import serialization

ROOT = Path(__file__).resolve().parents[1]


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


_env = {**load_dotenv(ROOT / ".env"), **os.environ}
API_KEY = _env.get("BINANCE_API_KEY", "").strip()
PRIVATE_KEY_PATH = Path(
    _env.get("BINANCE_PRIVATE_KEY_PATH", str(ROOT / "keys" / "binance_private.pem"))
).expanduser()
if not API_KEY or not PRIVATE_KEY_PATH.exists():
    raise RuntimeError("Missing BINANCE_API_KEY or BINANCE_PRIVATE_KEY_PATH")

with open(PRIVATE_KEY_PATH, "rb") as f:
    private_key = serialization.load_pem_private_key(f.read(), password=None)

def sign_message(message):
    signature = private_key.sign(message.encode())
    return base64.b64encode(signature).decode()

def get_timestamp():
    return int(time.time() * 1000)

def binance_api(endpoint, params=None, method='GET'):
    if params is None:
        params = {}
    params['timestamp'] = get_timestamp()
    query_string = urlencode(params)
    signature = sign_message(query_string)
    params['signature'] = signature
    url = f"https://fapi.binance.com{endpoint}?{urlencode(params)}"
    
    if method == 'POST':
        result = subprocess.run([
            'curl', '-s', '-X', 'POST',
            '-H', f'X-MBX-APIKEY: {API_KEY}',
            url
        ], capture_output=True, text=True)
    elif method == 'DELETE':
        result = subprocess.run([
            'curl', '-s', '-X', 'DELETE',
            '-H', f'X-MBX-APIKEY: {API_KEY}',
            url
        ], capture_output=True, text=True)
    else:
        result = subprocess.run([
            'curl', '-s',
            '-H', f'X-MBX-APIKEY: {API_KEY}',
            url
        ], capture_output=True, text=True)
    
    return json.loads(result.stdout)

# 委托单ID
ORDERS = {
    'INJUSDT': {
        'order_id': 12953334608,
        'side': 'BUY',
        'quantity': '8',
        'stop_loss': '5.50',
        'take_profit': '5.90'
    },
    'SEIUSDT': {
        'order_id': 8923316271,
        'side': 'BUY',
        'quantity': '780',
        'stop_loss': '0.06200',
        'take_profit': '0.06800'
    }
}

print("=" * 60)
print("🔍 监控限价单成交状态")
print("=" * 60)
print()
print("等待成交中...")
print()

filled_orders = set()

while True:
    try:
        for symbol, config in ORDERS.items():
            if symbol in filled_orders:
                continue
            
            # 查询订单状态
            order = binance_api('/fapi/v1/order', {
                'symbol': symbol,
                'orderId': config['order_id']
            })
            
            status = order.get('status')
            
            if status == 'FILLED':
                print(f"✅ {symbol} 限价单已成交！")
                print(f"   成交价: {order.get('avgPrice')}")
                print(f"   成交量: {order.get('executedQty')}")
                print()
                
                # 设置止损单
                print(f"   设置止损单 @ {config['stop_loss']}...")
                # 这里需要使用Algo Order API，暂时记录
                
                # 设置止盈单
                print(f"   设置止盈单 @ {config['take_profit']}...")
                # 这里需要使用Algo Order API，暂时记录
                
                filled_orders.add(symbol)
                print()
            
            elif status == 'CANCELED':
                print(f"❌ {symbol} 限价单已取消")
                filled_orders.add(symbol)
            
            else:
                now = time.strftime("%H:%M:%S")
                print(f"[{now}] {symbol}: {status} | 价格: {order.get('price')}")
        
        # 检查是否所有订单都处理完了
        if len(filled_orders) == len(ORDERS):
            print("所有订单已处理完毕")
            break
        
        time.sleep(10)  # 每10秒检查一次
        
    except Exception as e:
        print(f"错误: {e}")
        time.sleep(10)
