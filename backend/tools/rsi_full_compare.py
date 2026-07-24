"""
全面对比：
- 服务器主值 (已收盘 RSI)
- 服务器 live 值 (含未收盘 K 线)
- 重算的"币安网页应该显示的实时 RSI"（用最新价当假 close）
确认两者各自对齐。
"""
import json
import sys
import urllib.request
import datetime

sys.stdout.reconfigure(encoding='utf-8')

SERVER = "http://43.133.72.69:3003"
ADMIN = ("admin", "admin@2026")
SYMBOLS = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT"]
TFS = ["1m", "5m", "15m", "30m"]


def http_json(url, method="GET", body=None, headers=None):
    req = urllib.request.Request(url, method=method, headers=headers or {})
    if body is not None:
        req.add_header("Content-Type", "application/json")
        req.data = json.dumps(body).encode()
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def wilder_rsi_state(closes, period=6):
    """返回 (rsi_value, avg_gain, avg_loss, last_close)。"""
    if len(closes) < period + 1:
        return None, None, None, None
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [max(d, 0) for d in deltas]
    losses = [max(-d, 0) for d in deltas]
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period-1) + gains[i]) / period
        avg_l = (avg_l * (period-1) + losses[i]) / period
    rsi = 100 if avg_l == 0 else 100 - 100 / (1 + avg_g/avg_l)
    return rsi, avg_g, avg_l, closes[-1]


def peek_rsi(avg_g, avg_l, last_close, latest_price, period=6):
    """模拟"再算一根，close = latest_price"，不改状态。"""
    delta = latest_price - last_close
    g = max(delta, 0); l = max(-delta, 0)
    ag = (avg_g * (period-1) + g) / period
    al = (avg_l * (period-1) + l) / period
    return 100 if al == 0 else 100 - 100 / (1 + ag/al)


tok = http_json(f"{SERVER}/api/auth/login", "POST", {"username": ADMIN[0], "password": ADMIN[1]})["access_token"]
rt = http_json(f"{SERVER}/api/control/runtime", headers={"Authorization": f"Bearer {tok}"})

print(f"\n{'symbol':<10} {'tf':<4} {'svr主':<8} {'svr实时':<10} {'币安已收盘':<12} {'币安实时(此刻)':<14} {'主-币安已收盘':<14} {'实时-币安实时':<14}")
print("-" * 110)

for sym in SYMBOLS:
    pos = next(p for p in rt["positions"] if p["symbol"] == sym)
    mark = pos.get("mark_price") or 0
    for tf in TFS:
        klines = http_json(f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval={tf}&limit=500")
        closes = [float(k[4]) for k in klines[:-1]]
        binance_current_close = float(klines[-1][4])

        ref, avg_g, avg_l, last_close = wilder_rsi_state(closes, 6)
        binance_live = peek_rsi(avg_g, avg_l, last_close, binance_current_close)

        svr_main = pos["indicators"][tf]["rsi"]
        svr_live = pos["indicators"][tf].get("rsi_live")

        diff_main = svr_main - ref
        diff_live = (svr_live - binance_live) if svr_live is not None else None

        print(f"{sym:<10} {tf:<4} {svr_main:<8} "
              f"{(svr_live if svr_live is not None else '-'):<10} "
              f"{ref:<12.4f} {binance_live:<14.4f} "
              f"{diff_main:<+14.4f} "
              f"{(diff_live if diff_live is not None else '-'):<14}")
