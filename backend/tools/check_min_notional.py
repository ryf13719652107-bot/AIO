"""检查每个币种当前最小开仓需求。"""
import asyncio, os, sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

from app.config import get_settings
from app.exchange.binance_rest import BinanceFuturesClient


SYMBOLS = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT"]

# 模拟您的配置
CAPITAL = 100        # USDT
PCT_LIST = [2, 2, 3, 3, 4]   # 开仓 + 补1 + 补2 + 补3 + 补4
LEVERAGE = 20


async def main():
    s = get_settings()
    client = BinanceFuturesClient(s.BINANCE_API_KEY, s.BINANCE_API_SECRET, s.BINANCE_TESTNET, paper=False)
    await client.init()

    print(f"\n{'='*92}")
    print(f"  配置: 本金 {CAPITAL} USDT, 杠杆 {LEVERAGE}x, 开仓/补仓比例 {PCT_LIST}")
    print(f"{'='*92}\n")

    print(f"{'币种':<10} {'当前价':<12} {'step_size':<10} {'最小qty':<12} {'min_notional':<12} {'最小可下notional':<16}")
    print("-" * 92)

    for sym in SYMBOLS:
        mark = await client.get_mark_price(sym)
        step = client._symbol_filters.get(sym, {}).get("step_size", 0.001)
        min_q = client.min_qty(sym)
        min_n = client.min_notional(sym)
        min_tradable = max(min_q * mark, min_n)
        print(f"{sym:<10} {mark:<12.2f} {step:<10} {min_q:<12} {min_n:<12} {min_tradable:<16.2f}")

    print(f"\n{'='*92}")
    print(f"  每次开/补仓 notional 检验（本金 {CAPITAL} × 比例 × {LEVERAGE}x）")
    print(f"{'='*92}\n")
    print(f"{'币种':<10}", end="")
    labels = ["开仓 2%", "补1 2%", "补2 3%", "补3 3%", "补4 4%"]
    for l in labels:
        print(f"{l:<14}", end="")
    print()
    print("-" * 92)

    for sym in SYMBOLS:
        mark = await client.get_mark_price(sym)
        min_n = client.min_notional(sym)
        min_q = client.min_qty(sym)
        print(f"{sym:<10}", end="")
        remaining = CAPITAL
        for pct in PCT_LIST:
            margin = remaining * pct / 100
            notional = margin * LEVERAGE
            qty_raw = notional / mark
            qty = client.round_qty(sym, qty_raw)
            ok = qty >= min_q and qty * mark >= min_n
            actual_notional = qty * mark
            mark_str = f"{actual_notional:.1f}U {'OK' if ok else 'X'}"
            print(f"{mark_str:<14}", end="")
            if ok:
                remaining -= margin
        print()

    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
