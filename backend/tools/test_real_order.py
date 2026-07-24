"""
独立测试下单逻辑：5x 杠杆 + 市价开仓 + 30 秒后市价平仓。

用法：
  cd /opt/binance-celue1/backend
  source .venv/bin/activate
  python tools/test_real_order.py

注意:
- 强制真实下单 (无视 .env 的 PAPER_TRADING)
- 默认用 ETHUSDT 0.005 ETH (约 $10 名义价值，5x 下用 $2 保证金)
- 价格 30 秒内一般波动 <0.3%，亏损极限约 $0.03 + 手续费 $0.01 = 总成本约 $0.04
- 跑前先在币安切换到「单一资产模式」，否则逐仓设置会被拒绝（但不影响交易）
"""
import asyncio
import os
import sys
from datetime import datetime

# Windows 默认 GBK 不能打印 emoji，强制 utf-8
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

from app.config import get_settings
from app.exchange.binance_rest import BinanceFuturesClient

# ============ 可调参数 ============
SYMBOL = "ETHUSDT"
LEVERAGE = 5
QTY = 0.01              # 0.01 ETH ≈ $20 notional（币安最低 20）, $4 margin @ 5x
DIRECTION = "SHORT"     # "SHORT" 或 "LONG"
WAIT_SEC = 30
MIN_NOTIONAL = 20.0     # 币安合约最小名义价值
# ==================================


def banner(msg, char="="):
    print(f"\n{char*64}")
    print(f"  {msg}")
    print(char*64)


def ts():
    return datetime.now().strftime("%H:%M:%S")


async def main():
    settings = get_settings()
    if not settings.BINANCE_API_KEY or not settings.BINANCE_API_SECRET:
        print("❌ 未配置 BINANCE_API_KEY / BINANCE_API_SECRET，请先填 backend/.env")
        return

    print(f"\n⚠️  这是真实下单测试，会真扣手续费 + 真承担价格风险")
    print(f"⚠️  标的: {SYMBOL}  方向: {DIRECTION}  数量: {QTY}  杠杆: {LEVERAGE}x")
    print(f"⚠️  testnet: {settings.BINANCE_TESTNET}")

    # 强制走真实下单（无视 .env 的 PAPER_TRADING）
    client = BinanceFuturesClient(
        api_key=settings.BINANCE_API_KEY,
        api_secret=settings.BINANCE_API_SECRET,
        testnet=settings.BINANCE_TESTNET,
        paper=False,
    )

    banner("步骤 0. 连接币安 + 拉余额 + markPrice")
    await client.init()
    bal = await client.get_account_balance()
    mark = await client.get_mark_price(SYMBOL)
    qty = QTY
    notional = qty * mark
    # 自动调高数量满足币安最低 notional 20 USDT
    if notional < MIN_NOTIONAL:
        qty = client.round_qty(SYMBOL, (MIN_NOTIONAL + 0.5) / mark)
        notional = qty * mark
        print(f"[{ts()}] ⚠️  原定 qty 名义价值 {QTY*mark:.2f} < {MIN_NOTIONAL}，自动上调到 {qty}")
    margin = notional / LEVERAGE
    print(f"[{ts()}] 账户可用余额: {bal:.4f} USDT")
    print(f"[{ts()}] {SYMBOL} markPrice: {mark}")
    print(f"[{ts()}] 实际下单: {qty} {SYMBOL}, notional={notional:.2f}, 保证金={margin:.2f} USDT")
    if bal < margin * 1.5:
        print(f"❌ 余额不足。需要至少 {margin*1.5:.2f} USDT 才安全测试")
        await client.close()
        return

    banner("步骤 1. 设置 5x 逐仓")
    try:
        await client.setup_symbol(SYMBOL, LEVERAGE, "ISOLATED")
        print(f"[{ts()}] ✓ 已设置 {SYMBOL} 为 {LEVERAGE}x 逐仓")
    except Exception as e:
        print(f"[{ts()}] ⚠️  setup 警告（可能账号在多资产模式）: {e}")
        print(f"[{ts()}]    继续下单，将使用账号当前默认保证金模式")

    side = "SELL" if DIRECTION == "SHORT" else "BUY"

    banner(f"步骤 2. 市价 {DIRECTION} 开仓 {qty} {SYMBOL}")
    coid_open = client.gen_client_order_id("test-open")
    try:
        order = await client.market_order(
            SYMBOL, side=side, position_side=DIRECTION,
            quantity=qty, client_order_id=coid_open,
        )
        fill = float(order.get("avgPrice") or 0)
        print(f"[{ts()}] ✓ 开仓 OK")
        print(f"          clientOrderId: {order.get('clientOrderId')}")
        print(f"          status: {order.get('status')}")
        print(f"          成交均价: {fill}")
    except Exception as e:
        print(f"[{ts()}] ❌ 开仓失败: {e}")
        await client.close()
        return

    banner(f"步骤 3. 等待 {WAIT_SEC} 秒（每 5s 查询一次持仓和浮亏）")
    for i in range(WAIT_SEC // 5):
        await asyncio.sleep(5)
        try:
            p = await client.get_position(SYMBOL, position_side=DIRECTION)
            amt = float(p.get("positionAmt", 0))
            entry = float(p.get("entryPrice", 0))
            mark_now = float(p.get("markPrice", 0)) or await client.get_mark_price(SYMBOL)
            upnl = float(p.get("unRealizedProfit", 0))
            print(f"[{ts()}] t+{(i+1)*5:>2}s  qty={amt}  entry={entry}  mark={mark_now}  uPnL={upnl:+.4f}")
        except Exception as e:
            print(f"[{ts()}] t+{(i+1)*5}s  查询失败: {e}")

    banner(f"步骤 4. 市价 reduceOnly 平仓（模拟止盈逻辑）")
    coid_close = client.gen_client_order_id("test-close")
    try:
        order = await client.close_position(
            SYMBOL, direction=DIRECTION, quantity=qty,
            client_order_id=coid_close,
        )
        fill = float(order.get("avgPrice") or 0)
        print(f"[{ts()}] ✓ 平仓 OK")
        print(f"          clientOrderId: {order.get('clientOrderId')}")
        print(f"          status: {order.get('status')}")
        print(f"          成交均价: {fill}")
    except Exception as e:
        print(f"[{ts()}] ❌ 平仓失败: {e}")
        await client.close()
        return

    await asyncio.sleep(2)
    try:
        p = await client.get_position(SYMBOL, position_side=DIRECTION)
        amt = float(p.get("positionAmt", 0))
        if abs(amt) < 1e-8:
            print(f"[{ts()}] ✓ 持仓已清零")
        else:
            print(f"[{ts()}] ⚠️  持仓仍剩余: {amt}")
    except Exception as e:
        print(f"[{ts()}] 查询失败: {e}")

    bal_after = await client.get_account_balance()
    diff = bal_after - bal
    banner("📊 结果汇总")
    print(f"  开仓前余额: {bal:.4f} USDT")
    print(f"  平仓后余额: {bal_after:.4f} USDT")
    print(f"  净盈亏(含手续费): {diff:+.4f} USDT")
    print(f"  开仓 clientOrderId: {coid_open}")
    print(f"  平仓 clientOrderId: {coid_close}")
    print(f"  → 去币安网页「交易历史」可对账")

    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
