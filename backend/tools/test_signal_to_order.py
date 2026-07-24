"""
端到端测试：模拟 EMA 金叉/死叉信号 → 信号判定 → 真实开仓 → 验证交易所持仓 → 平仓。

测试链路:
  SymbolIndicators.update() → CrossDetector → SignalEngine.evaluate() → BinanceFuturesClient.market_order()

用法:
  cd backend
  .venv\Scripts\activate      (Windows)
  source .venv/bin/activate    (Linux)
  python tools/test_signal_to_order.py

注意:
- 真实下单，会扣手续费
- 默认 XRPUSDT（跟你遇到问题的币种一致）
- 测试完自动平仓
"""
import asyncio
import os
import sys
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

from app.config import get_settings
from app.exchange.binance_rest import BinanceFuturesClient
from app.strategy.indicators import SymbolIndicators
from app.strategy.position import SymbolPosition
from app.strategy.signal import SignalEngine

# ============ 可调参数 ============
SYMBOL = "XRPUSDT"
LEVERAGE = 5
CAPITAL_USDT = 20.0       # 用于计算开仓数量的虚拟本金
POSITION_PCT = 100.0      # 仓位比例 100%
EMA_FAST = 7
EMA_SLOW = 25
# ==================================


def ts():
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def banner(msg, char="="):
    print(f"\n{char*70}")
    print(f"  {msg}")
    print(f"{char*70}")


async def main():
    settings = get_settings()
    if not settings.BINANCE_API_KEY or not settings.BINANCE_API_SECRET:
        print("❌ 未配置 BINANCE_API_KEY / BINANCE_API_SECRET")
        return

    print(f"\n⚠️  端到端测试: 信号 → 开仓 → 验证 → 平仓")
    print(f"⚠️  标的: {SYMBOL}  杠杆: {LEVERAGE}x  本金: {CAPITAL_USDT} USDT")
    print(f"⚠️  testnet: {settings.BINANCE_TESTNET}")

    # ========== 步骤 0: 初始化交易所客户端 ==========
    banner("步骤 0. 初始化交易所客户端")
    client = BinanceFuturesClient(
        api_key=settings.BINANCE_API_KEY,
        api_secret=settings.BINANCE_API_SECRET,
        testnet=settings.BINANCE_TESTNET,
        paper=False,
    )
    await client.init()
    print(f"[{ts()}] ✓ 客户端初始化完成")
    print(f"[{ts()}]   持仓模式: {'双向 Hedge' if client.hedge_mode else '单向 One-way'}")

    bal = await client.get_account_balance()
    mark = await client.get_mark_price(SYMBOL)
    print(f"[{ts()}]   账户余额: {bal:.4f} USDT")
    print(f"[{ts()}]   {SYMBOL} markPrice: {mark}")

    try:
        await client.setup_symbol(SYMBOL, LEVERAGE, "ISOLATED")
        print(f"[{ts()}] ✓ 已设置 {SYMBOL} 为 {LEVERAGE}x 逐仓")
    except Exception as e:
        print(f"[{ts()}] ⚠️  setup 警告: {e}")

    # ========== 步骤 1: 用历史 K 线 bootstrap 指标 ==========
    banner("步骤 1. Bootstrap EMA 指标（拉 200 根历史 K 线）")
    indicators = SymbolIndicators(fast_period=EMA_FAST, slow_period=EMA_SLOW)
    klines = await client.get_klines(SYMBOL, "3m", limit=200)
    closed_klines = klines[:-1]  # 去掉最后一根未收盘的
    closes = [float(k[4]) for k in closed_klines]
    indicators.bootstrap(closes)

    snap = indicators.snapshot()
    print(f"[{ts()}] ✓ EMA bootstrap 完成")
    print(f"[{ts()}]   EMA({EMA_FAST}) = {snap['ema_fast']}")
    print(f"[{ts()}]   EMA({EMA_SLOW}) = {snap['ema_slow']}")
    print(f"[{ts()}]   diff = {snap['diff']}")
    print(f"[{ts()}]   last_cross = {snap['last_cross']}")
    print(f"[{ts()}]   ready = {snap['ready']}")

    if not indicators.ready:
        print(f"❌ 指标未就绪，无法测试")
        await client.close()
        return

    # ========== 步骤 2: 模拟交叉信号（强制制造） ==========
    banner("步骤 2. 模拟 EMA 交叉信号")
    position = SymbolPosition(symbol=SYMBOL)
    signal_engine = SignalEngine()

    # 先看当前快慢线关系，决定制造什么信号
    ema_f = indicators.ema_fast.value
    ema_s = indicators.ema_slow.value
    current_diff = ema_f - ema_s

    print(f"[{ts()}] 当前快慢线差: {current_diff:.6f}")

    if current_diff > 0:
        # 快线在上 → 制造死叉信号 → 开空
        test_signal = "DEATH"
        expected_direction = "SHORT"
        print(f"[{ts()}] 快线在上 → 模拟 DEATH(死叉) 信号 → 预期开空")
    else:
        # 快线在下 → 制造金叉信号 → 开多
        test_signal = "GOLDEN"
        expected_direction = "LONG"
        print(f"[{ts()}] 快线在下 → 模拟 GOLDEN(金叉) 信号 → 预期开多")

    # 调用 SignalEngine.evaluate
    action = signal_engine.evaluate(indicators, position, test_signal)
    print(f"[{ts()}] SignalEngine 判定结果:")
    print(f"[{ts()}]   action.type   = {action.type}")
    print(f"[{ts()}]   action.reason = {action.reason}")

    if action.type == "NONE":
        print(f"\n❌ SignalEngine 返回了 NONE！信号被吞了！")
        print(f"   可能原因: indicators.ready={indicators.ready}, position.direction={position.direction}")
        await client.close()
        return

    # ========== 步骤 3: 执行真实开仓（复刻 engine._do_open 的逻辑） ==========
    banner(f"步骤 3. 执行真实开仓 — {expected_direction}")

    # 计算仓位（复刻 engine._do_open 的完整计算逻辑）
    margin = CAPITAL_USDT * (POSITION_PCT / 100.0)
    notional = margin * LEVERAGE
    qty = client.round_qty(SYMBOL, notional / mark if mark > 0 else 0)

    print(f"[{ts()}] 计算参数:")
    print(f"[{ts()}]   capital = {CAPITAL_USDT}")
    print(f"[{ts()}]   margin  = {margin:.2f}")
    print(f"[{ts()}]   notional = {notional:.2f}")
    print(f"[{ts()}]   qty = {qty}")
    print(f"[{ts()}]   min_qty = {client.min_qty(SYMBOL)}")
    print(f"[{ts()}]   min_notional = {client.min_notional(SYMBOL)}")

    if qty < client.min_qty(SYMBOL):
        print(f"\n❌ 数量 {qty} < 最小数量 {client.min_qty(SYMBOL)}，无法开仓！")
        await client.close()
        return

    actual_notional = qty * mark
    if actual_notional < client.min_notional(SYMBOL):
        print(f"\n❌ notional {actual_notional:.2f} < 币安最小 {client.min_notional(SYMBOL)}，无法开仓！")
        await client.close()
        return

    side = "BUY" if expected_direction == "LONG" else "SELL"
    coid = client.gen_client_order_id(prefix=f"test-signal-{SYMBOL.lower()}")

    print(f"[{ts()}] 下单参数:")
    print(f"[{ts()}]   symbol = {SYMBOL}")
    print(f"[{ts()}]   side = {side}")
    print(f"[{ts()}]   position_side = {expected_direction}")
    print(f"[{ts()}]   quantity = {qty}")
    print(f"[{ts()}]   clientOrderId = {coid}")

    try:
        order = await client.market_order(
            SYMBOL, side=side, position_side=expected_direction,
            quantity=qty, client_order_id=coid,
        )
        fill_price = float(order.get("avgPrice") or 0)
        status = order.get("status", "UNKNOWN")
        print(f"\n[{ts()}] ✅ 开仓成功!")
        print(f"[{ts()}]   orderId = {order.get('orderId')}")
        print(f"[{ts()}]   clientOrderId = {order.get('clientOrderId')}")
        print(f"[{ts()}]   status = {status}")
        print(f"[{ts()}]   avgPrice = {fill_price}")
    except Exception as e:
        print(f"\n[{ts()}] ❌ 开仓失败! 异常: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        await client.close()
        return

    # ========== 步骤 4: 验证交易所持仓 ==========
    banner("步骤 4. 验证交易所持仓（等 2 秒后查询）")
    await asyncio.sleep(2)

    try:
        pos_info = await client.get_position(SYMBOL, position_side=expected_direction)
        pos_amt = float(pos_info.get("positionAmt", 0))
        pos_entry = float(pos_info.get("entryPrice", 0))
        pos_side = pos_info.get("positionSide", "?")
        pos_upnl = float(pos_info.get("unRealizedProfit", 0))
        print(f"[{ts()}] 交易所持仓信息:")
        print(f"[{ts()}]   positionAmt   = {pos_amt}")
        print(f"[{ts()}]   entryPrice    = {pos_entry}")
        print(f"[{ts()}]   positionSide  = {pos_side}")
        print(f"[{ts()}]   unRealizedPnL = {pos_upnl:+.6f}")

        if abs(pos_amt) > 0:
            print(f"\n[{ts()}] ✅ 验证通过！交易所确认有持仓 qty={pos_amt}")
        else:
            print(f"\n[{ts()}] ❌ 验证失败！交易所没有持仓！")
            print(f"[{ts()}]    开仓返回了 status={status}，但交易所查不到仓位")
            print(f"[{ts()}]    可能是下单被拒绝或延迟")
    except Exception as e:
        print(f"[{ts()}] ❌ 查询持仓失败: {e}")

    # ========== 步骤 5: 平仓清理 ==========
    banner("步骤 5. 平仓清理")
    try:
        coid_close = client.gen_client_order_id("test-close")
        close_order = await client.close_position(
            SYMBOL, direction=expected_direction, quantity=qty,
            client_order_id=coid_close,
        )
        close_fill = float(close_order.get("avgPrice") or 0)
        print(f"[{ts()}] ✓ 平仓成功  avgPrice={close_fill}")
    except Exception as e:
        print(f"[{ts()}] ❌ 平仓失败: {e}")
        print(f"[{ts()}]    请手动到币安平仓！")

    # ========== 步骤 6: 最终余额对账 ==========
    await asyncio.sleep(1)
    bal_after = await client.get_account_balance()
    diff = bal_after - bal
    banner("📊 测试结果汇总")
    print(f"  EMA 信号:     {test_signal} → 动作: {action.type}")
    print(f"  开仓方向:     {expected_direction}")
    print(f"  开仓数量:     {qty} {SYMBOL}")
    print(f"  开仓均价:     {fill_price}")
    print(f"  平仓均价:     {close_fill if 'close_fill' in dir() else 'N/A'}")
    print(f"  开仓前余额:   {bal:.4f} USDT")
    print(f"  平仓后余额:   {bal_after:.4f} USDT")
    print(f"  净盈亏(含手续费): {diff:+.4f} USDT")
    print(f"\n  → 去币安「交易历史」搜索 clientOrderId: {coid} 可对账")

    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
