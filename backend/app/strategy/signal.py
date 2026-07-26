"""
RSI + 成交量策略规则判定器。

开仓/加仓：实时（未收盘 live VOL + 已收盘 RSI）；已启用条件 AND。
加仓：两级严格顺序，各最多一次。
TP2：所选周期收盘 RSI。
TP1/SL2：1m 收盘武装基准后按实时 mark 判定。
TP1：浮盈≥保证金×目标比例（默认50%）；已取消保本与移动止盈。
"""

from dataclasses import dataclass
from typing import Literal, Optional

from app.schemas import SideSignalConfig, StrategyParams
from app.strategy.indicators import SymbolIndicators
from app.strategy.position import SymbolPosition

ActionType = Literal[
    "NONE",
    "OPEN_LONG", "OPEN_SHORT",
    "ADD",
    "CLOSE_TP1", "CLOSE_TP2",
    "CLOSE_SL1", "CLOSE_SL2",
]


@dataclass
class Action:
    type: ActionType = "NONE"
    reason: str = ""
    trigger_key: str = ""


def _side_enabled(cfg: SideSignalConfig) -> bool:
    return bool(cfg.enable_rsi or cfg.enable_vol)


def _check_side_signal(
    cfg: SideSignalConfig,
    *,
    rsi: Optional[float],
    vol_latest: Optional[float],
    vol_avg: Optional[float],
    long_side: bool,
) -> tuple[bool, str]:
    """已启用条件 AND；无启用项返回 False。"""
    if not _side_enabled(cfg):
        return False, ""

    parts: list[str] = []

    if cfg.enable_rsi:
        if rsi is None:
            return False, ""
        if long_side:
            if rsi > cfg.rsi_threshold:
                return False, ""
            parts.append(f"RSI={rsi:.1f}≤{cfg.rsi_threshold:g}")
        else:
            if rsi < cfg.rsi_threshold:
                return False, ""
            parts.append(f"RSI={rsi:.1f}≥{cfg.rsi_threshold:g}")

    if cfg.enable_vol:
        if vol_latest is None or vol_avg is None or vol_avg <= 0:
            return False, ""
        threshold = vol_avg * cfg.vol_mult
        if vol_latest <= threshold:
            return False, ""
        parts.append(f"VOL={vol_latest:.2f}>{cfg.vol_mult:g}×均={threshold:.2f}")

    return True, "+".join(parts)


def _tf_metrics(ind: SymbolIndicators, timeframe: str, lookback: int):
    """
    指标口径（实时开仓）：
    - RSI：有 live 未收盘价时用 peek 估算；否则用已收盘 Wilder 值
    - VOL：有 live 未收盘量时用 live vs 最近 N 根已收盘均量；
           否则用最新已收盘量 vs 其前 N 根均量（avg_prior）
    """
    t = ind.get(timeframe)
    rsi = t.rsi.value if t.rsi.initialized else None
    if t.live_close is not None and t.rsi.initialized:
        peeked = t.rsi.peek(t.live_close)
        if peeked is not None:
            rsi = peeked
    if t.live_volume is not None:
        vol_latest = t.live_volume
        vol_avg = t.volume.avg_closed(lookback)
    else:
        vol_latest = t.volume.latest
        vol_avg = t.volume.avg_prior(lookback)
    return t, rsi, vol_latest, vol_avg


class StrategyRules:
    """无状态规则引擎。"""

    @staticmethod
    def long_entry_ready(params: StrategyParams, ind: SymbolIndicators) -> tuple[bool, str]:
        entry = params.entry_conditions
        if not entry.enable_long:
            return False, ""
        cfg = entry.long
        if not _side_enabled(cfg):
            return False, ""
        _, rsi, vol_latest, vol_avg = _tf_metrics(ind, params.timeframe, cfg.vol_lookback)
        return _check_side_signal(
            cfg, rsi=rsi, vol_latest=vol_latest, vol_avg=vol_avg, long_side=True,
        )

    @staticmethod
    def short_entry_ready(params: StrategyParams, ind: SymbolIndicators) -> tuple[bool, str]:
        entry = params.entry_conditions
        if not entry.enable_short:
            return False, ""
        cfg = entry.short
        if not _side_enabled(cfg):
            return False, ""
        _, rsi, vol_latest, vol_avg = _tf_metrics(ind, params.timeframe, cfg.vol_lookback)
        return _check_side_signal(
            cfg, rsi=rsi, vol_latest=vol_latest, vol_avg=vol_avg, long_side=False,
        )

    @staticmethod
    def add_level_ready(params: StrategyParams, ind: SymbolIndicators,
                        pos: SymbolPosition, level: int) -> tuple[bool, str]:
        add = params.add_conditions
        if not add.enabled:
            return False, ""
        if not pos.can_add_level(level):
            return False, ""
        level_cfg = add.level1 if level == 1 else add.level2
        if not level_cfg.enabled:
            return False, ""
        cfg = level_cfg.long if pos.direction == "LONG" else level_cfg.short
        if not _side_enabled(cfg):
            return False, ""
        # 加仓两侧 VOL 参数通常镜像；以当前方向 cfg 为准，lookback 取该侧
        lookback = cfg.vol_lookback
        # level 配置里 long/short 的 lookback 可能不一致，兜底用 entry
        if lookback <= 0:
            lookback = params.entry_conditions.long.vol_lookback
        _, rsi, vol_latest, vol_avg = _tf_metrics(ind, params.timeframe, lookback)
        ok, reason = _check_side_signal(
            cfg, rsi=rsi, vol_latest=vol_latest, vol_avg=vol_avg,
            long_side=(pos.direction == "LONG"),
        )
        if not ok:
            return False, ""
        return True, f"加仓{level}: {reason}"

    @classmethod
    def check_tp2(cls, params: StrategyParams, ind: SymbolIndicators,
                  pos: SymbolPosition, *, trigger_interval: str) -> Optional[Action]:
        if pos.is_flat or not params.exit.enable_tp2:
            return None
        if trigger_interval != params.timeframe:
            return None
        t = ind.get(params.timeframe)
        rsi = t.rsi.value if t.rsi.initialized else None
        if rsi is None:
            return None
        if pos.direction == "LONG" and rsi >= params.exit.tp2_long_rsi:
            return Action(
                type="CLOSE_TP2",
                reason=f"TP2 多 RSI={rsi:.1f}≥{params.exit.tp2_long_rsi:g}",
            )
        if pos.direction == "SHORT" and rsi <= params.exit.tp2_short_rsi:
            return Action(
                type="CLOSE_TP2",
                reason=f"TP2 空 RSI={rsi:.1f}≤{params.exit.tp2_short_rsi:g}",
            )
        return None

    @classmethod
    def check_price_exits(cls, params: StrategyParams, pos: SymbolPosition,
                          mark: float) -> Optional[Action]:
        """
        实时退出：
        - 等待基准期：仅 SL2
        - 武装后：TP1 保证金盈利目标 + SL2（不再保本/移动止盈）
        """
        if pos.is_flat or mark <= 0:
            return None
        ex = params.exit
        pnl = pos.unrealized_pnl(mark)

        # 等待 1m 武装期间：只跑硬止损 SL2
        if not pos.baseline_armed:
            if pos.pending_baseline and ex.enable_sl2 and pos.margin > 0:
                loss_limit = pos.margin * (ex.sl2_margin_loss_pct / 100.0)
                if pnl <= -loss_limit:
                    return Action(
                        type="CLOSE_SL2",
                        reason=(
                            f"SL2 浮亏(等基准): {pnl:.4f} ≤ -保证金×"
                            f"{ex.sl2_margin_loss_pct:g}% (-{loss_limit:.4f})"
                        ),
                    )
            return None

        # SL2：浮亏 ≥ 保证金比例
        if ex.enable_sl2 and pos.margin > 0:
            loss_limit = pos.margin * (ex.sl2_margin_loss_pct / 100.0)
            if pnl <= -loss_limit:
                return Action(
                    type="CLOSE_SL2",
                    reason=(
                        f"SL2 浮亏: {pnl:.4f} ≤ -保证金×{ex.sl2_margin_loss_pct:g}% "
                        f"(-{loss_limit:.4f})"
                    ),
                )

        # TP1：回报率（浮盈/保证金）达到目标（默认 50%，同币安仓位回报率）
        if ex.enable_tp1 and pos.margin > 0:
            target = pos.margin * (ex.tp1_profit_pct / 100.0)
            if pnl >= target:
                roe_pct = (pnl / pos.margin) * 100.0
                return Action(
                    type="CLOSE_TP1",
                    reason=(
                        f"TP1 止盈: 回报率={roe_pct:.2f}% ≥ {ex.tp1_profit_pct:g}% "
                        f"(浮盈={pnl:.4f}, 保证金={pos.margin:.4f})"
                    ),
                )

        return None

    @classmethod
    def evaluate_entry(cls, params: StrategyParams, ind: SymbolIndicators,
                       pos: SymbolPosition, *, allow_open: bool = True) -> Action:
        """实时开仓/加仓评估（不依赖收盘触发）。"""
        if not pos.is_flat:
            for level in (1, 2):
                ok, reason = cls.add_level_ready(params, ind, pos, level)
                if ok:
                    return Action(
                        type="ADD",
                        reason=reason,
                        trigger_key=f"add{level}",
                    )
            return Action()

        if not allow_open:
            return Action()

        long_ok, long_reason = cls.long_entry_ready(params, ind)
        if long_ok:
            return Action(type="OPEN_LONG", reason=f"开多: {long_reason}")

        short_ok, short_reason = cls.short_entry_ready(params, ind)
        if short_ok:
            return Action(type="OPEN_SHORT", reason=f"开空: {short_reason}")

        return Action()

    @classmethod
    def evaluate(cls, params: StrategyParams, ind: SymbolIndicators,
                 pos: SymbolPosition, *, allow_open: bool = True,
                 trigger_interval: str = "1m",
                 mode: str = "all") -> Action:
        """
        mode:
          - entry: 仅开仓/加仓（实时）
          - tp2: 仅 TP2（所选周期收盘）
          - all: 持仓先 TP2 再加仓；空仓开仓（兼容旧测试）
        """
        tf = params.timeframe
        if mode == "entry":
            return cls.evaluate_entry(params, ind, pos, allow_open=allow_open)

        if mode == "tp2":
            if trigger_interval != tf or pos.is_flat:
                return Action()
            tp2 = cls.check_tp2(params, ind, pos, trigger_interval=trigger_interval)
            return tp2 or Action()

        # all
        if trigger_interval != tf:
            return Action()

        if not pos.is_flat:
            tp2 = cls.check_tp2(params, ind, pos, trigger_interval=trigger_interval)
            if tp2:
                return tp2
            return cls.evaluate_entry(params, ind, pos, allow_open=False)

        return cls.evaluate_entry(params, ind, pos, allow_open=allow_open)
