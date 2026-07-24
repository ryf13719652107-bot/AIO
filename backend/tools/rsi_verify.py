"""验证 RSI(6) 算法是否与币安网页显示一致"""
import json
import sys
import datetime
import urllib.request

sys.stdout.reconfigure(encoding='utf-8')


def fetch_klines(symbol="BTCUSDT", interval="1m", limit=50):
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}"
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read())


def wilder_rsi(closes, period=6):
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [max(d, 0) for d in deltas]
    losses = [max(-d, 0) for d in deltas]
    if len(gains) < period:
        return []
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    rsi_list = [None] * (period + 1)
    rsi_list[period] = 100 if avg_l == 0 else 100 - 100 / (1 + avg_g/avg_l)
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period-1) + gains[i]) / period
        avg_l = (avg_l * (period-1) + losses[i]) / period
        rsi_list.append(100 if avg_l == 0 else 100 - 100 / (1 + avg_g/avg_l))
    return rsi_list


def main():
    for sym in ["BTCUSDT", "ETHUSDT"]:
        data = fetch_klines(sym, "1m", 50)
        closes = [float(k[4]) for k in data]
        open_times = [int(k[0]) for k in data]
        rsi_full = wilder_rsi(closes, period=6)

        print(f"\n=== {sym} 1m 最近 8 根 K线 + RSI(6) ===")
        print(f"{'时间':<10} {'状态':<14} {'close':<12} {'RSI(6)':<10}")
        for i in range(-8, 0):
            t = datetime.datetime.fromtimestamp(open_times[i]/1000).strftime('%H:%M:%S')
            status = '已收盘' if i < -1 else '⚠进行中'
            r = rsi_full[i]
            r_str = f"{r:.2f}" if r is not None else "-"
            print(f"{t:<10} {status:<14} {closes[i]:<12.2f} {r_str:<10}")
        print(f"\n→ 我们系统应该显示 RSI = {rsi_full[-2]:.2f} （最新已收盘）")
        print(f"→ 币安网页 RSI = {rsi_full[-1]:.2f} （含进行中那根）")


if __name__ == "__main__":
    main()
