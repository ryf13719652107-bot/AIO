/** 策略参数默认值，与后端 schemas.py (strategy_version=3) 保持一致 */
export function defaultStrategyParams() {
  return {
    strategy_version: 3,
    leverage_mode: 'follow',
    leverage: 10,
    screening: {
      enable_mcap: true,
      mcap_min_usd: 15_000_000,
      mcap_max_usd: 10_000_000_000,
      enable_price: true,
      price_max_usd: 30,
      enable_volume: true,
      volume_min_usd: 20_000_000,
      volume_max_usd: 3_000_000_000,
      refresh_hours: 1,
    },
    entry: {
      enable_long: true,
      enable_short: true,
      long: {
        ema_5m: true,        // 5m EMA7 > EMA25
        ema_5m_99: true,     // 5m EMA7&25 均 > EMA99
        rsi: true,           // 5m RSI(6) < 阈值
        rsi_threshold: 65,
        macd_dif: true,      // 5m DIF > DEA
        macd_hist: true,     // 5m MACD柱<0且连续3根缩小
        ema_15m_cross: true, ema_15m_over99: true,
        ema_30m_cross: true, ema_30m_over99: true,
        ema_60m_cross: true, ema_60m_over99: true,
        ema_120m_cross: true, ema_120m_over99: true,
      },
      short: {
        ema_5m: true,        // 5m EMA7 < EMA25
        ema_5m_99: true,     // 5m EMA7&25 均 < EMA99
        rsi: true,           // 5m RSI(6) > 阈值
        rsi_threshold: 50,
        macd_dif: true,      // 5m DIF < DEA
        macd_hist: true,     // 5m MACD柱>0且连续3根缩小
        ema_15m_cross: true, ema_15m_over99: true,
        ema_30m_cross: true, ema_30m_over99: true,
        ema_60m_cross: true, ema_60m_over99: true,
        ema_120m_cross: true, ema_120m_over99: true,
      },
    },
    order_plan: {
      order_margin_usdt: 1,  // 每单保证金 USDT (1-100)
      enable_first: true,    // 满足条件直接开首仓
      add_ema7: true,        // 回踩 ≤5m EMA7
      add_ema25: true,       // 回踩 ≤5m EMA25，仅一次
      add_ema99: true,       // 回踩 ≤5m EMA99，仅一次
      add_15m: true,         // 15m 趋势条件，仅一次
      add_30m: true,         // 30m 趋势条件，仅一次
    },
    exit: {
      enable_tp1: true,      // 1m EMA25<EMA99（多）/ >（空）
      enable_tp2: true,      // 5m EMA7<EMA25
      enable_tp3: true,      // 5m EMA25<EMA99
      enable_tp4: true,      // 15m EMA7<EMA25
      enable_tp5: true,      // 5m VOL > 前30根均量×倍数
      tp5_vol_mult: 10,
      enable_tp6: true,      // 多周期 RSI 分批
      tp6: {
        long: {
          t5m: { enabled: true, rsi_threshold: 93, close_pct: 10 },
          t15m: { enabled: true, rsi_threshold: 95, close_pct: 20 },
          t30m: { enabled: true, rsi_threshold: 97, close_pct: 20 },
          t60m: { enabled: true, rsi_threshold: 97, close_pct: 30 },
          t60m_120m: { enabled: true, rsi_threshold: 96, close_pct: 20 },
        },
        short: {
          t5m: { enabled: true, rsi_threshold: 10, close_pct: 10 },
          t15m: { enabled: true, rsi_threshold: 7, close_pct: 30 },
          t30m: { enabled: true, rsi_threshold: 7, close_pct: 20 },
          t60m: { enabled: true, rsi_threshold: 8, close_pct: 30 },
          t60m_120m: { enabled: true, rsi_threshold: 8, close_pct: 10 },
        },
      },
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
  // 旧版（v1/v2）结构：忽略旧策略字段，仅保留筛选与杠杆设置
  const isLegacy = Number(raw.strategy_version) < 3
    || raw.entry_conditions != null
    || raw.ema_fast != null
  if (isLegacy) {
    const merged = { ...d }
    if (raw.screening && typeof raw.screening === 'object') {
      merged.screening = deepMerge(d.screening, raw.screening)
    }
    if (raw.leverage != null) merged.leverage = Number(raw.leverage) || 10
    if (raw.leverage_mode === 'follow' || raw.leverage_mode === 'manual') {
      merged.leverage_mode = raw.leverage_mode
    }
    return merged
  }
  // 旧 v3 合并开关拆分（保留勾选状态）：
  //   macd → macd_dif / macd_hist
  //   ema_15m/30m/60m/120m → ema_XX_cross / ema_XX_over99
  const rawEntry = { ...(raw.entry || {}) }
  for (const side of ['long', 'short']) {
    const s = rawEntry[side]
    if (s && typeof s === 'object') {
      let cur = s
      if ('macd' in s && !('macd_dif' in s)) {
        const { macd, ...rest } = cur
        cur = { ...rest, macd_dif: !!macd, macd_hist: !!macd }
      }
      for (const tf of ['15m', '30m', '60m', '120m']) {
        const key = `ema_${tf}`
        if (key in cur && !(`${key}_cross` in cur)) {
          const { [key]: old, ...rest } = cur
          cur = { ...rest, [`${key}_cross`]: !!old, [`${key}_over99`]: !!old }
        }
      }
      rawEntry[side] = cur
    }
  }
  return {
    ...deepMerge(d, raw),
    screening: deepMerge(d.screening, raw.screening || {}),
    entry: deepMerge(d.entry, rawEntry),
    order_plan: deepMerge(d.order_plan, raw.order_plan || {}),
    exit: deepMerge(d.exit, raw.exit || {}),
  }
}
