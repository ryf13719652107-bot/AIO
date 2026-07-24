"""
诊断 5/27 00:00 BTC 5m RSI 差异：
对比 Wilder（我们用的）vs Cutler（SMA）× 合约 vs 现货 4 种组合。
"""
import json
import sys
import datetime
import urllib.request

sys.stdout.reconfigure(encoding='utf-8')


def fetch(url):
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


def cutler_rsi(closes, period=6):
    """SMA 版 RSI（中国股民/有些平台默认）。"""
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [max(d, 0) for d in deltas]
    losses = [max(-d, 0) for d in deltas]
    rsi_list = [None] * period
    for i in range(period - 1, len(gains)):
        window_g = gains[i - period + 1 : i + 1]
        window_l = losses[i - period + 1 : i + 1]
        ag = sum(window_g) / period
        al = sum(window_l) / period
        rsi_list.append(100 if al == 0 else 100 - 100 / (1 + ag/al))
    return rsi_list


def ema_rsi(closes, period=6):
    """EMA 版 (TradingView 'rma' 行为)。"""
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [max(d, 0) for d in deltas]
    losses = [max(-d, 0) for d in deltas]
    alpha = 1 / period
    avg_g, avg_l = 0, 0
    rsi_list = [None]
    for i, (g, l) in enumerate(zip(gains, losses)):
        avg_g = alpha * g + (1 - alpha) * avg_g
        avg_l = alpha * l + (1 - alpha) * avg_l
        if i >= period - 1:
            rsi_list.append(100 if avg_l == 0 else 100 - 100 / (1 + avg_g/avg_l))
        else:
            rsi_list.append(None)
    return rsi_list


def analyze(label, klines):
    closes = [float(k[4]) for k in klines]
    times = [int(k[0]) for k in klines]
    rw = wilder_rsi(closes, 6)
    rc = cutler_rsi(closes, 6)
    re = ema_rsi(closes, 6)

    print(f"\n=== {label} 5m  最近 8 根 ===")
    print(f"{'OpenTime':<10} {'close':<12} {'Wilder':<10} {'Cutler':<10} {'EMA':<10}")
    for i in range(-8, 0):
        t = datetime.datetime.fromtimestamp(times[i]/1000).strftime('%m-%d %H:%M')
        w = f"{rw[i]:.2f}" if rw[i] is not None else "-"
        c = f"{rc[i]:.2f}" if rc[i] is not None else "-"
        e = f"{re[i]:.2f}" if re[i] is not None else "-"
        print(f"{t:<10} {closes[i]:<12.2f} {w:<10} {c:<10} {e:<10}")


# 5/27 00:00 UTC+8 = 5/26 16:00 UTC. 拉够多数据确保覆盖到这个点。
# 合约
fu = fetch("https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=5m&limit=100")
# 现货
sp = fetch("https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=5m&limit=100")

analyze("BTCUSDT 合约(Futures)", fu)
analyze("BTCUSDT 现货(Spot)", sp)
