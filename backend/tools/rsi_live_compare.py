"""精准定位：拉服务器值 + 拉币安原始 K 线现算 Wilder RSI(6)，
对每个币每个周期都打印对比，看是哪一个对不上。"""
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


def login():
    r = http_json(f"{SERVER}/api/auth/login", "POST", {"username": ADMIN[0], "password": ADMIN[1]})
    return r["access_token"]


def server_runtime(tok):
    return http_json(f"{SERVER}/api/control/runtime", headers={"Authorization": f"Bearer {tok}"})


def binance_klines(sym, tf, limit=500):
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval={tf}&limit={limit}"
    return http_json(url)


def wilder_rsi(closes, period=6):
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [max(d, 0) for d in deltas]
    losses = [max(-d, 0) for d in deltas]
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period-1) + gains[i]) / period
        avg_l = (avg_l * (period-1) + losses[i]) / period
    if avg_l == 0:
        return 100.0
    return 100 - 100 / (1 + avg_g/avg_l)


def main():
    tok = login()
    rt = server_runtime(tok)

    print(f"\n服务器 engine 启动于: {rt['started_at']}\n")
    print(f"{'symbol':<10} {'tf':<4} {'svr_RSI':<10} {'计算_RSI':<10} {'最新已收盘 K线 OpenTime':<24} {'close':<12} {'差':<8}")
    print("-" * 86)

    for sym in SYMBOLS:
        pos = next(p for p in rt["positions"] if p["symbol"] == sym)
        for tf in TFS:
            klines = binance_klines(sym, tf, 500)
            last_closed = klines[-2]
            last_open_time = int(last_closed[0])
            last_close = float(last_closed[4])

            closes = [float(k[4]) for k in klines[:-1]]
            ref = wilder_rsi(closes, 6)
            svr = pos["indicators"][tf]["rsi"]
            diff = (svr - ref) if (svr is not None and ref is not None) else None

            t_str = datetime.datetime.fromtimestamp(last_open_time/1000).strftime("%m-%d %H:%M:%S")
            print(f"{sym:<10} {tf:<4} {svr:<10} {ref:<10.4f} {t_str:<24} {last_close:<12} {diff:<+8.4f}")


if __name__ == "__main__":
    main()
