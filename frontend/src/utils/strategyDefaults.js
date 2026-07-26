/** 策略参数默认值，与后端 schemas.py (strategy_version=2) 保持一致 */
export function defaultStrategyParams() {
  return {
    strategy_version: 2,
    timeframe: '1m',
    position_pct: 2.0,
    rsi_period: 6,
    leverage_mode: 'follow',
    leverage: 20,
    screening: {
      enable_volume: true,
      volume_min_usd: 5_000_000,
      enable_mcap: true,
      mcap_min_usd: 8_000_000,
      enable_mcap_max: true,
      mcap_max_usd: 6_000_000_000,
      enable_price: true,
      price_max_usd: 20,
      refresh_hours: 1,
    },
    entry_conditions: {
      enable_long: true,
      enable_short: true,
      long: {
        enable_rsi: true,
        rsi_threshold: 10,
        enable_vol: true,
        vol_lookback: 30,
        vol_mult: 7,
      },
      short: {
        enable_rsi: true,
        rsi_threshold: 86,
        enable_vol: true,
        vol_lookback: 30,
        vol_mult: 7,
      },
    },
    add_conditions: {
      enabled: true,
      level1: {
        enabled: true,
        long: {
          enable_rsi: true,
          rsi_threshold: 8,
          enable_vol: true,
          vol_lookback: 30,
          vol_mult: 10,
        },
        short: {
          enable_rsi: true,
          rsi_threshold: 90,
          enable_vol: true,
          vol_lookback: 30,
          vol_mult: 10,
        },
      },
      level2: {
        enabled: true,
        long: {
          enable_rsi: true,
          rsi_threshold: 6,
          enable_vol: true,
          vol_lookback: 30,
          vol_mult: 15,
        },
        short: {
          enable_rsi: true,
          rsi_threshold: 93,
          enable_vol: true,
          vol_lookback: 30,
          vol_mult: 15,
        },
      },
    },
    exit: {
      enable_tp1: true,
      tp1_profit_pct: 50,
      enable_tp2: true,
      tp2_long_rsi: 85,
      tp2_short_rsi: 15,
      enable_sl1: false,
      enable_sl2: true,
      sl2_margin_loss_pct: 10,
    },
  }
}

function deepMerge(base, over) {
  if (!over || typeof over !== 'object') return base
  const out = { ...base }
  for (const [k, v] of Object.entries(over)) {
    if (v && typeof v === 'object' && !Array.isArray(v) && typeof base[k] === 'object' && base[k] && !Array.isArray(base[k])) {
      out[k] = deepMerge(base[k], v)
    } else if (v !== undefined && v !== null && v !== '') {
      out[k] = v
    }
  }
  return out
}

export function mergeStrategyParams(raw) {
  const d = defaultStrategyParams()
  if (!raw || typeof raw !== 'object') return d
  // 旧版 EMA 配置：忽略旧结构，仅保留 screening / position_pct / leverage 等通用字段
  const isV1 = raw.strategy_version < 2 || raw.ema_fast != null || raw.features || raw.take_profit
  if (isV1 && !raw.exit) {
    const merged = { ...d }
    if (raw.screening) merged.screening = deepMerge(d.screening, raw.screening)
    if (raw.position_pct != null && Number(raw.position_pct) > 0) {
      merged.position_pct = Number(raw.position_pct) === 1 ? 2 : Number(raw.position_pct)
    }
    if (raw.leverage != null) merged.leverage = Number(raw.leverage) || 20
    if (raw.rsi_period != null) merged.rsi_period = Number(raw.rsi_period) || 6
    if (raw.timeframe === '1m' || raw.timeframe === '3m' || raw.timeframe === '5m') {
      merged.timeframe = raw.timeframe
    }
    if (raw.leverage_mode === 'follow' || raw.leverage_mode === 'manual') {
      merged.leverage_mode = raw.leverage_mode
    }
    merged.strategy_version = 2
    return merged
  }
  const exit = deepMerge(d.exit, raw.exit || {})
  if (exit.tp1_profit_pct == null || Number(exit.tp1_profit_pct) <= 0) {
    exit.tp1_profit_pct = 50
  }
  exit.enable_sl1 = false
  delete exit.tp1_drawdown_pct
  return {
    ...deepMerge(d, raw),
    screening: deepMerge(d.screening, raw.screening || {}),
    entry_conditions: deepMerge(d.entry_conditions, raw.entry_conditions || {}),
    add_conditions: deepMerge(d.add_conditions, raw.add_conditions || {}),
    exit,
    strategy_version: 2,
  }
}
