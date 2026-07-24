"""
诊断脚本：直接拉取 K 线数据并模拟交叉检测，验证为什么没开仓。
"""
import asyncio
import sys
sys.path.insert(0, '.')

from app.exchange.binance_rest import BinanceFuturesClient
from app.strategy.indicators import SymbolIndicators
from app.config import get_settings

settings = get_settings()

async def main():
    client = BinanceFuturesClient(
        api_key=settings.BINANCE_API_KEY,
        api_secret=settings.BINANCE_API_SECRET,
        testnet=settings.BINANCE_TESTNET,
        paper=False,
    )
    await client.init()
    
    print(f"=== 持仓模式: {'双向 Hedge' if client.hedge_mode else '单向 One-way / 检测失败'} ===")
    
    symbols = ["SOLUSDT", "BTCUSDT", "ETHUSDT", "XRPUSDT", "ZECUSDT"]
    interval = "3m"
    
    for sym in symbols:
        print(f"\n{'='*60}")
        print(f"== {sym} ({interval}) ==")
        
        # 拉 200 根历史 K 线
        klines = await client.get_klines(sym, interval, limit=200)
        print(f"  拉到 {len(klines)} 根 K 线")
        
        if not klines:
            continue
        
        # 去掉最后一根（未收盘）
        closed_klines = klines[:-1]
        print(f"  已收盘 K 线: {len(closed_klines)} 根")
        
        # Bootstrap 指标
        ind = SymbolIndicators(fast_period=7, slow_period=25)
        closes = [float(k[4]) for k in closed_klines]
        
        # 逐根处理，记录最后几次交叉
        crosses = []
        for i, c in enumerate(closes):
            signal = ind.update(c)
            if signal:
                crosses.append((i, c, signal))
        
        print(f"  EMA7 = {ind.ema_fast.value:.6f}")
        print(f"  EMA25 = {ind.ema_slow.value:.6f}")
        print(f"  diff = {ind.ema_fast.value - ind.ema_slow.value:.6f}")
        print(f"  last_cross = {ind._last_cross}")
        print(f"  交叉检测器 prev_diff = {ind.cross.prev_diff}")
        
        if crosses:
            print(f"\n  最近 5 次交叉信号:")
            for idx, price, sig in crosses[-5:]:
                print(f"    K线#{idx}: close={price}, signal={sig}")
        else:
            print("  ⚠️ 从未检测到交叉信号!")
        
        # 模拟最后一根未收盘 K 线
        last_k = klines[-1]
        last_close = float(last_k[4])
        last_open_time = int(last_k[0])
        
        # 如果用最后一根（当前K线）的 close 再走一步，会不会触发交叉？
        test_ind = SymbolIndicators(fast_period=7, slow_period=25)
        test_ind.bootstrap(closes)  # 重新 bootstrap
        test_signal = test_ind.update(last_close)
        print(f"\n  当前未收盘K线 close={last_close}")
        print(f"  如果收盘在此价格, 信号 = {test_signal}")
        
        # 检查 min_qty 和 min_notional
        min_q = client.min_qty(sym)
        min_n = client.min_notional(sym)
        mark = await client.get_mark_price(sym)
        print(f"\n  当前 mark price = {mark}")
        print(f"  min_qty = {min_q}")
        print(f"  min_notional = {min_n}")
        
        # 模拟仓位计算（假设 200 USDT，20x，20%）
        capital = 200.0
        position_pct = 20.0
        leverage = 20
        margin = capital * (position_pct / 100.0)
        notional = margin * leverage
        qty = client.round_qty(sym, notional / mark if mark > 0 else 0)
        actual_notional = qty * mark
        print(f"\n  模拟开仓计算:")
        print(f"    可用资金 = {capital} USDT")
        print(f"    保证金 = {margin} USDT ({position_pct}%)")
        print(f"    名义价值 = {notional} USDT ({leverage}x)")
        print(f"    下单数量 = {qty}")
        print(f"    实际名义 = {actual_notional:.2f} USDT")
        print(f"    qty >= min_qty? {qty >= min_q} ({qty} >= {min_q})")
        print(f"    notional >= min_notional? {actual_notional >= min_n} ({actual_notional:.2f} >= {min_n})")
    
    # 测试账户余额（会因为 API Key 问题失败）
    print(f"\n{'='*60}")
    print("== 测试账户余额查询 ==")
    try:
        balance = await client.get_account_balance()
        print(f"  USDT 可用余额: {balance}")
    except Exception as e:
        print(f"  ❌ 余额查询失败: {e}")
    
    await client.close()

asyncio.run(main())
