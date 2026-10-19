"""
多周期 EMA/MACD/RSI 策略规则判定器（v3，双向持仓）。

- 开仓：5m 收盘 K 判定（边沿触发由引擎负责），已启用条件 AND，全部用已收盘数据。
- 回踩加仓：实时 mark 价对 5m EMA7/25/99，需先站上（多）/跌破（空）EMA 才武装，
  武装后回踩触发，各档仅一次。
- 趋势加仓：15m/30m 收盘满足 EMA 趋势条件，各仅一次。
- 止盈 TP1-6：全部按已收盘 K 线判定，先到先执行；全平条件优先于 TP6 分批。
- 保本损：TP6 任一档触发后武装，由引擎按实时 mark 盯价（触及开仓均价全平）。
"""

from dataclasses import dataclass, field
from typing import Literal, Optional

from app.schemas import SIGNAL_TIMEFRAME, StrategyParams
from app.strategy.indicators import SymbolIndicators
from app.strategy.position import SymbolPosition

ActionType = Literal[
    "NONE",
    "OPEN_LONG", "OPEN_SHORT",
    "ADD",
    "CLOSE_TP1", "CLOSE_TP2", "CLOSE_TP3", "CLOSE_TP4", "CLOSE_TP5", "CLOSE_TP6",
]

# 回踩加仓档 → 5m EMA 属性名
PULLBACK_LEVELS = {
    "ema7": "ema7",
    "ema25": "ema25",
    "ema99": "ema99",
}
# 趋势加仓档 → 周期
TREND_LEVELS = {
    "t15m": "15m",
    "t30m": "30m",
}
# TP6 档 → 周期（t60m_120m 特殊：60m 与 120m 同时判定）
# 注意：引擎 K 线键为 1h/2h（60 分钟/120 分钟即 1h/2h）
TP6_LEVEL_TFS = {
    "t5m": ("5m",),
    "t15m": ("15m",),
    "t30m": ("30m",),
    "t60m": ("1h",),
    "t60m_120m": ("1h", "2h"),
}


@dataclass
class Action:
    type: ActionType = "NONE"
    reason: str = ""
    trigger_key: str = ""
    close_pct: float = 100.0     # TP6 分批：平掉整笔订单总仓位的百分比


def _cross_ok(t, long_side: bool) -> bool:
    """高周期条件A：多 EMA7>EMA25；空 EMA7<EMA25。"""
    e7, e25 = t.ema7.value, t.ema25.value
    if e7 is None or e25 is None:
        return False
    return e7 > e25 if long_side else e7 < e25


def _trend_ok(t, long_side: bool) -> bool:
    """趋势条件（加仓档 / 高周期条件B口径）：多 EMA7>99 且 EMA25>99；空镜像。"""
    e7, e25, e99 = t.ema7.value, t.ema25.value, t.ema99.value
    if e7 is None or e25 is None or e99 is None:
        return False
    if long_side:
        return e7 > e99 and e25 > e99
    return e7 < e99 and e25 < e99


class StrategyRules:
    """无状态规则引擎。"""

    # ---------- 开仓（5m 收盘判定） ----------
    @staticmethod
    def entry_ready(params: StrategyParams, ind: SymbolIndicators,
                    direction: str) -> tuple[bool, str]:
        entry = params.entry
        long_side = direction == "LONG"
        if long_side and not entry.enable_long:
            return False, ""
        if not long_side and not entry.enable_short:
            return False, ""
        cfg = entry.long if long_side else entry.short

        t5 = ind.get(SIGNAL_TIMEFRAME)
        parts: list[str] = []
        any_enabled = False

        def require(enabled: bool, ok: bool, label: str) -> bool:
            nonlocal any_enabled
            if not enabled:
                return True
            any_enabled = True
            if not ok:
                return False
            parts.append(label)
            return True

        def _val(x):
            return x is not None

        e7, e25, e99 = t5.ema7.value, t5.ema25.value, t5.ema99.value
        ema_ready = _val(e7) and _val(e25) and _val(e99)

        if not require(
            cfg.ema_5m,
            ema_ready and (e7 > e25 if long_side else e7 < e25),
            f"5m EMA7{'>' if long_side else '<'}EMA25",
        ):
            return False, ""
        if not require(
            cfg.ema_5m_99,
            ema_ready and (
                (e7 > e99 and e25 > e99) if long_side else (e7 < e99 and e25 < e99)
            ),
            f"5m EMA7&25{'均>' if long_side else '均<'}EMA99",
        ):
            return False, ""

        rsi = t5.rsi.value
        rsi_ok = rsi is not None and (
            rsi < cfg.rsi_threshold if long_side else rsi > cfg.rsi_threshold
        )
        if not require(
            cfg.rsi, rsi_ok,
            f"RSI={rsi:.1f}{'<' if long_side else '>'}{cfg.rsi_threshold:g}",
        ):
            return False, ""

        # 5m MACD 两个独立条件（文档第6/7行）：DIF>(<)DEA；柱0轴下(上)连续3根缩小
        macd = t5.macd
        dif_ok = (
            macd.dif is not None and macd.dea is not None
            and (macd.dif > macd.dea if long_side else macd.dif < macd.dea)
        )
        if not require(
            cfg.macd_dif, dif_ok,
            f"5m DIF{'>' if long_side else '<'}DEA",
        ):
            return False, ""
        hist_ok = (
            macd.hist_shrinking_below_zero() if long_side
            else macd.hist_shrinking_above_zero()
        )
        if not require(
            cfg.macd_hist, hist_ok,
            "MACD柱<0连3缩" if long_side else "MACD柱>0连3缩",
        ):
            return False, ""

        # 高周期开仓条件（文档每周期两项独立开关）：
        #   cross：EMA7>(<)EMA25；over99：EMA7&25 均>(<)EMA99
        # 键映射：60 分钟→1h，120 分钟→2h（与引擎 K 线订阅键一致）
        for attr_cross, attr_over, tf in (
            ("ema_15m_cross", "ema_15m_over99", "15m"),
            ("ema_30m_cross", "ema_30m_over99", "30m"),
            ("ema_60m_cross", "ema_60m_over99", "1h"),
            ("ema_120m_cross", "ema_120m_over99", "2h"),
        ):
            if not require(
                getattr(cfg, attr_cross), _cross_ok(ind.get(tf), long_side),
                f"{tf} EMA7{'>' if long_side else '<'}25",
            ):
                return False, ""
            if not require(
                getattr(cfg, attr_over), _trend_ok(ind.get(tf), long_side),
                f"{tf} 7&25均{'>' if long_side else '<'}99",
            ):
                return False, ""

        if not any_enabled:
            return False, ""
        return True, "+".join(parts)

    # ---------- 回踩加仓（mark 实时判定，武装后触发，各档仅一次） ----------
    @classmethod
    def check_pullback(cls, params: StrategyParams, ind: SymbolIndicators,
                       pos: SymbolPosition, mark: float) -> Optional[Action]:
        if pos.is_flat or pos.add_blocked or mark <= 0:
            return None
        plan = params.order_plan
        t5 = ind.get(SIGNAL_TIMEFRAME)
        long_side = pos.direction == "LONG"

        for key, enabled in (
            ("ema7", plan.add_ema7),
            ("ema25", plan.add_ema25),
            ("ema99", plan.add_ema99),
        ):
            if not enabled or pos.is_leg_fired(key):
                continue
            ema_val = getattr(t5, PULLBACK_LEVELS[key]).value
            if ema_val is None:
                continue
            if long_side:
                if mark > ema_val:
                    pos.set_pullback_armed(key, True)
                elif pos.get_pullback_armed(key) and mark <= ema_val:
                    return Action(
                        type="ADD", trigger_key=key,
                        reason=f"回踩≤5m {key.upper()}={ema_val:.6f} (mark={mark:.6f})",
                    )
            else:
                if mark < ema_val:
                    pos.set_pullback_armed(key, True)
                elif pos.get_pullback_armed(key) and mark >= ema_val:
                    return Action(
                        type="ADD", trigger_key=key,
                        reason=f"回踩≥5m {key.upper()}={ema_val:.6f} (mark={mark:.6f})",
                    )
        return None

    # ---------- 趋势加仓（15m/30m 收盘判定，各仅一次） ----------
    @classmethod
    def trend_add_ready(cls, params: StrategyParams, ind: SymbolIndicators,
                        pos: SymbolPosition, tf: str) -> Optional[Action]:
        if pos.is_flat or pos.add_blocked:
            return None
        plan = params.order_plan
        enabled = plan.add_15m if tf == "15m" else plan.add_30m
        if not enabled:
            return None
        key = "t15m" if tf == "15m" else "t30m"
        if pos.is_leg_fired(key):
            return None
        if _trend_ok(ind.get(tf), pos.direction == "LONG"):
            return Action(
                type="ADD", trigger_key=key,
                reason=f"{tf} EMA7&25 均{'>' if pos.direction == 'LONG' else '<'}EMA99",
            )
        return None

    # ---------- 止盈（收盘判定；全平优先于 TP6） ----------
    @classmethod
    def check_exit(cls, params: StrategyParams, ind: SymbolIndicators,
                   pos: SymbolPosition, trigger_interval: str) -> Optional[Action]:
        if pos.is_flat:
            return None
        ex = params.exit
        long_side = pos.direction == "LONG"

        def ema_cross(tf: str, a: str, b: str, label: str) -> Optional[Action]:
            """多：ema(a)<ema(b) 触发；空：ema(a)>ema(b) 触发。"""
            t = ind.get(tf)
            va, vb = getattr(t, a).value, getattr(t, b).value
            if va is None or vb is None:
                return None
            hit = (va < vb) if long_side else (va > vb)
            if not hit:
                return None
            return Action(
                type=label,
                reason=f"{tf} {a.upper()}={va:.6f}{'<' if long_side else '>'}{b.upper()}={vb:.6f}",
            )

        # TP1：1m EMA25 vs EMA99
        if ex.enable_tp1 and trigger_interval == "1m":
            act = ema_cross("1m", "ema25", "ema99", "CLOSE_TP1")
            if act:
                return act

        # TP2/TP3/TP5：5m 收盘
        if trigger_interval == "5m":
            if ex.enable_tp2:
                act = ema_cross("5m", "ema7", "ema25", "CLOSE_TP2")
                if act:
                    return act
            if ex.enable_tp3:
                act = ema_cross("5m", "ema25", "ema99", "CLOSE_TP3")
                if act:
                    return act
            if ex.enable_tp5:
                vol_latest = ind.get("5m").volume.latest
                vol_avg = ind.get("5m").volume.avg_prior(30)
                if (
                    vol_latest is not None and vol_avg is not None and vol_avg > 0
                    and vol_latest > vol_avg * ex.tp5_vol_mult
                ):
                    return Action(
                        type="CLOSE_TP5",
                        reason=(
                            f"5m VOL={vol_latest:.2f}>前30均量{vol_avg:.2f}"
                            f"×{ex.tp5_vol_mult:g}"
                        ),
                    )

        # TP4：15m EMA7 vs EMA25
        if ex.enable_tp4 and trigger_interval == "15m":
            act = ema_cross("15m", "ema7", "ema25", "CLOSE_TP4")
            if act:
                return act

        # TP6：分批（各档仅一次；RSI 全部用已收盘值）
        if not ex.enable_tp6:
            return None
        tp6_cfg = ex.tp6.long if long_side else ex.tp6.short
        for key, tfs in TP6_LEVEL_TFS.items():
            if pos.is_tp6_fired(key):
                continue
            level = getattr(tp6_cfg, key)
            if not level.enabled:
                continue
            if trigger_interval not in tfs:
                continue
            rsis = [ind.get(tf).rsi.value for tf in tfs]
            if any(r is None for r in rsis):
                continue
            if long_side:
                hit = all(r >= level.rsi_threshold for r in rsis)
            else:
                hit = all(r <= level.rsi_threshold for r in rsis)
            if hit:
                rsi_txt = "/".join(f"{r:.1f}" for r in rsis)
                return Action(
                    type="CLOSE_TP6",
                    trigger_key=key,
                    close_pct=level.close_pct,
                    reason=(
                        f"TP6 {key} RSI={rsi_txt} "
                        f"{'≥' if long_side else '≤'}{level.rsi_threshold:g} "
                        f"平{level.close_pct:g}%"
                    ),
                )
        return None

    # ---------- TP6 实时盯价（mark 试算 RSI，各档仅一次） ----------
    @classmethod
    def tp6_live_check(cls, params: StrategyParams, ind: SymbolIndicators,
                       pos: SymbolPosition, mark: float) -> Optional[Action]:
        """TP6 多周期 RSI 分批的实时版：用最新 mark 试算"若现在收盘"的各周期 RSI。

        口径与收盘版完全一致（档位/阈值/方向/双周期 AND/各档仅一次），
        仅数据源改为实时试算，满足即触发（先到先执行，不等周期收盘）。
        """
        if pos.is_flat or pos.breakeven_armed:
            return None
        ex = params.exit
        if not ex.enable_tp6:
            return None
        long_side = pos.direction == "LONG"
        tp6_cfg = ex.tp6.long if long_side else ex.tp6.short
        for key, tfs in TP6_LEVEL_TFS.items():
            if pos.is_tp6_fired(key):
                continue
            level = getattr(tp6_cfg, key)
            if not level.enabled:
                continue
            rsis: list[float] = []
            for tf in tfs:
                t = ind.get(tf)
                r = t._live_view(mark).get("rsi")
                if r is None:
                    r = t.rsi.value
                if r is None:
                    rsis = []
                    break
                rsis.append(r)
            if not rsis:
                continue
            hit = (
                all(r >= level.rsi_threshold for r in rsis)
                if long_side
                else all(r <= level.rsi_threshold for r in rsis)
            )
            if hit:
                rsi_txt = "/".join(f"{r:.1f}" for r in rsis)
                return Action(
                    type="CLOSE_TP6",
                    trigger_key=key,
                    close_pct=level.close_pct,
                    reason=(
                        f"TP6实时 {key} RSI={rsi_txt} "
                        f"{'≥' if long_side else '≤'}{level.rsi_threshold:g} "
                        f"平{level.close_pct:g}%"
                    ),
                )
        return None
