<template>
  <el-card class="params-card" shadow="never">
    <template #header>
      <b>策略参数设置</b>
      <span class="hint">RSI+成交量 · 修改后点上方「保存配置」</span>
    </template>

    <template v-if="ready">
      <section class="block">
        <div class="block-title">基础</div>
        <div class="row">
          <div class="field">
            <label>全局周期</label>
            <el-select v-model="params.timeframe" size="small" style="width:120px">
              <el-option label="1 分钟" value="1m" />
              <el-option label="3 分钟" value="3m" />
              <el-option label="5 分钟" value="5m" />
            </el-select>
          </div>
          <div class="field">
            <label>仓位比例 %（剩余保证金）</label>
            <el-input-number v-model="params.position_pct" :min="0.1" :max="100" :precision="1" :step="0.5" size="small" controls-position="right" />
          </div>
          <div class="field">
            <label>RSI 周期</label>
            <el-input-number v-model="params.rsi_period" :min="2" :max="30" size="small" controls-position="right" />
          </div>
          <div class="field">
            <label>杠杆模式</label>
            <el-select v-model="params.leverage_mode" size="small" style="width:140px">
              <el-option label="跟随交易所" value="follow" />
              <el-option label="手动设置" value="manual" />
            </el-select>
          </div>
          <div class="field" v-if="params.leverage_mode === 'manual'">
            <label>手动杠杆</label>
            <el-input-number v-model="params.leverage" :min="1" :max="125" size="small" controls-position="right" />
          </div>
        </div>
        <div class="unit" style="margin-top:8px">开仓/加仓：实时（未收盘价估算 RSI + 未收盘放量）；TP2：所选周期收盘；TP1/SL1/SL2：等下一根 1m K 收盘建立基准后按实时价触发</div>
      </section>

      <section class="block">
        <div class="block-title">选币筛选（均可开关 + 改阈值）</div>
        <div class="grid-3">
          <div class="param-box">
            <div class="param-head">
              <el-switch v-model="params.screening.enable_volume" />
              <span>24h 成交额下限</span>
            </div>
            <el-input-number v-model="volumeMinWan" :min="0" :step="100" :precision="0" :disabled="!params.screening.enable_volume" size="small" controls-position="right" />
            <div class="unit">单位：万美元 · 默认 500</div>
          </div>
          <div class="param-box">
            <div class="param-head">
              <el-switch v-model="params.screening.enable_mcap" />
              <span>市值下限</span>
            </div>
            <el-input-number v-model="mcapMinWan" :min="0" :step="100" :precision="0" :disabled="!params.screening.enable_mcap" size="small" controls-position="right" />
            <div class="unit">单位：万美元 · 默认 800</div>
          </div>
          <div class="param-box">
            <div class="param-head">
              <el-switch v-model="params.screening.enable_mcap_max" />
              <span>市值上限</span>
            </div>
            <el-input-number v-model="mcapMaxYi" :min="0" :step="10" :precision="0" :disabled="!params.screening.enable_mcap_max" size="small" controls-position="right" />
            <div class="unit">单位：亿美元 · 默认 60</div>
          </div>
          <div class="param-box">
            <div class="param-head">
              <el-switch v-model="params.screening.enable_price" />
              <span>币价上限 (USD)</span>
            </div>
            <el-input-number v-model="params.screening.price_max_usd" :min="0" :precision="2" :step="1" :disabled="!params.screening.enable_price" size="small" controls-position="right" />
            <div class="unit">默认 20 美元</div>
          </div>
        </div>
        <div class="row" style="margin-top:10px">
          <div class="field">
            <label>选币刷新间隔（小时）</label>
            <el-input-number v-model="params.screening.refresh_hours" :min="0.5" :max="24" :step="0.5" size="small" controls-position="right" />
          </div>
        </div>
      </section>

      <section class="block">
        <div class="block-title">开仓（已启用条件 AND · 全关则禁该方向）</div>
        <div class="tp-row">
          <el-checkbox v-model="params.entry_conditions.enable_long"><b>允许开多</b></el-checkbox>
        </div>
        <div class="tp-row" v-if="params.entry_conditions.enable_long">
          <el-checkbox v-model="params.entry_conditions.long.enable_rsi">RSI ≤</el-checkbox>
          <el-input-number v-model="params.entry_conditions.long.rsi_threshold" :min="0" :max="100" :precision="1" size="small" controls-position="right" />
          <el-checkbox v-model="params.entry_conditions.long.enable_vol">VOL &gt; 前</el-checkbox>
          <el-input-number v-model="params.entry_conditions.long.vol_lookback" :min="5" :max="100" size="small" controls-position="right" />
          <span class="unit">根均量 ×</span>
          <el-input-number v-model="params.entry_conditions.long.vol_mult" :min="1" :max="50" :precision="1" size="small" controls-position="right" />
        </div>
        <div class="tp-row">
          <el-checkbox v-model="params.entry_conditions.enable_short"><b>允许开空</b></el-checkbox>
        </div>
        <div class="tp-row" v-if="params.entry_conditions.enable_short">
          <el-checkbox v-model="params.entry_conditions.short.enable_rsi">RSI ≥</el-checkbox>
          <el-input-number v-model="params.entry_conditions.short.rsi_threshold" :min="0" :max="100" :precision="1" size="small" controls-position="right" />
          <el-checkbox v-model="params.entry_conditions.short.enable_vol">VOL &gt; 前</el-checkbox>
          <el-input-number v-model="params.entry_conditions.short.vol_lookback" :min="5" :max="100" size="small" controls-position="right" />
          <span class="unit">根均量 ×</span>
          <el-input-number v-model="params.entry_conditions.short.vol_mult" :min="1" :max="50" :precision="1" size="small" controls-position="right" />
        </div>
      </section>

      <section class="block">
        <div class="block-title">加仓（两级顺序 · 各最多一次 · 每级用剩余保证金%）</div>
        <div class="tp-row">
          <el-checkbox v-model="params.add_conditions.enabled"><b>启用加仓</b></el-checkbox>
        </div>
        <template v-if="params.add_conditions.enabled">
          <div class="unit" style="margin-bottom:8px">一级加仓</div>
          <div class="tp-row">
            <el-checkbox v-model="params.add_conditions.level1.enabled">启用一级</el-checkbox>
          </div>
          <div class="tp-row" v-if="params.add_conditions.level1.enabled">
            <span class="unit">多 RSI≤</span>
            <el-checkbox v-model="params.add_conditions.level1.long.enable_rsi" />
            <el-input-number v-model="params.add_conditions.level1.long.rsi_threshold" :min="0" :max="100" :precision="1" size="small" controls-position="right" />
            <span class="unit">空 RSI≥</span>
            <el-checkbox v-model="params.add_conditions.level1.short.enable_rsi" />
            <el-input-number v-model="params.add_conditions.level1.short.rsi_threshold" :min="0" :max="100" :precision="1" size="small" controls-position="right" />
            <el-checkbox v-model="params.add_conditions.level1.long.enable_vol">VOL×</el-checkbox>
            <el-input-number v-model="params.add_conditions.level1.long.vol_mult" :min="1" :max="50" :precision="1" size="small" controls-position="right" />
          </div>
          <div class="unit" style="margin:8px 0">二级加仓</div>
          <div class="tp-row">
            <el-checkbox v-model="params.add_conditions.level2.enabled">启用二级</el-checkbox>
          </div>
          <div class="tp-row" v-if="params.add_conditions.level2.enabled">
            <span class="unit">多 RSI≤</span>
            <el-checkbox v-model="params.add_conditions.level2.long.enable_rsi" />
            <el-input-number v-model="params.add_conditions.level2.long.rsi_threshold" :min="0" :max="100" :precision="1" size="small" controls-position="right" />
            <span class="unit">空 RSI≥</span>
            <el-checkbox v-model="params.add_conditions.level2.short.enable_rsi" />
            <el-input-number v-model="params.add_conditions.level2.short.rsi_threshold" :min="0" :max="100" :precision="1" size="small" controls-position="right" />
            <el-checkbox v-model="params.add_conditions.level2.long.enable_vol">VOL×</el-checkbox>
            <el-input-number v-model="params.add_conditions.level2.long.vol_mult" :min="1" :max="50" :precision="1" size="small" controls-position="right" />
          </div>
        </template>
      </section>

      <section class="block">
        <div class="block-title">止盈 / 止损（独立开关 · 先到先执行）</div>
        <div class="tp-row">
          <el-checkbox v-model="params.exit.enable_tp1">TP1 移动止盈（峰值回撤）</el-checkbox>
          <el-input-number v-model="params.exit.tp1_drawdown_pct" :min="1" :max="99" :precision="1" size="small" controls-position="right" />
          <span class="unit">%（相对最高浮盈，实时）</span>
        </div>
        <div class="tp-row">
          <el-checkbox v-model="params.exit.enable_tp2">TP2 RSI（所选周期收盘）</el-checkbox>
          <span class="unit">多 ≥</span>
          <el-input-number v-model="params.exit.tp2_long_rsi" :min="0" :max="100" :precision="1" size="small" controls-position="right" />
          <span class="unit">空 ≤</span>
          <el-input-number v-model="params.exit.tp2_short_rsi" :min="0" :max="100" :precision="1" size="small" controls-position="right" />
        </div>
        <div class="tp-row">
          <el-checkbox v-model="params.exit.enable_sl1">SL1 保本（浮盈降至 0，实时）</el-checkbox>
        </div>
        <div class="tp-row">
          <el-checkbox v-model="params.exit.enable_sl2">SL2 保证金浮亏</el-checkbox>
          <el-input-number v-model="params.exit.sl2_margin_loss_pct" :min="0.1" :max="100" :precision="1" size="small" controls-position="right" />
          <span class="unit">%（实时）</span>
        </div>
      </section>

      <section class="block hint-block">
        <div class="strategy-hint">
          <p><b>开多：</b>已勾选条件同时满足（默认 RSI≤10 且放量）</p>
          <p><b>开空：</b>默认 RSI≥86 且放量</p>
          <p><b>加仓：</b>一级→二级严格顺序（实时）；加仓后等下一根 1m 收盘重置退出基准</p>
          <p><b>退出：</b>TP1 峰值移动止盈 / SL1 保本 / SL2 浮亏（1m收盘后；等基准期仍有 SL2）；TP2 RSI（所选周期收盘）</p>
        </div>
      </section>
    </template>
    <div v-else class="loading">参数加载中…</div>
  </el-card>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { mergeStrategyParams } from '../utils/strategyDefaults'

const WAN = 10000
const YI = 100_000_000
const DEFAULT_VOLUME_WAN = 500
const DEFAULT_MCAP_WAN = 800
const DEFAULT_MCAP_MAX_YI = 60

const props = defineProps({
  params: { type: Object, required: true },
})

const ready = computed(() => !!(
  props.params
  && props.params.screening
  && props.params.entry_conditions
  && props.params.add_conditions
  && props.params.exit
))

const volumeMinWan = ref(DEFAULT_VOLUME_WAN)
const mcapMinWan = ref(DEFAULT_MCAP_WAN)
const mcapMaxYi = ref(DEFAULT_MCAP_MAX_YI)
let syncing = false

function usdToWan(usd, fallback) {
  const n = Number(usd)
  if (!Number.isFinite(n) || n <= 0) return fallback
  return Math.round(n / WAN)
}

function usdToYi(usd, fallback) {
  const n = Number(usd)
  if (!Number.isFinite(n) || n <= 0) return fallback
  return Math.round(n / YI)
}

function syncFromParams() {
  if (!props.params?.screening) return
  syncing = true
  volumeMinWan.value = usdToWan(props.params.screening.volume_min_usd, DEFAULT_VOLUME_WAN)
  mcapMinWan.value = usdToWan(props.params.screening.mcap_min_usd, DEFAULT_MCAP_WAN)
  mcapMaxYi.value = usdToYi(props.params.screening.mcap_max_usd, DEFAULT_MCAP_MAX_YI)
  props.params.screening.volume_min_usd = volumeMinWan.value * WAN
  props.params.screening.mcap_min_usd = mcapMinWan.value * WAN
  props.params.screening.mcap_max_usd = mcapMaxYi.value * YI
  syncing = false
}

function ensureDefaults() {
  if (!props.params) return
  const merged = mergeStrategyParams(props.params)
  Object.keys(merged).forEach((k) => {
    if (typeof merged[k] === 'object' && merged[k] !== null && !Array.isArray(merged[k])) {
      if (!props.params[k] || typeof props.params[k] !== 'object') {
        props.params[k] = JSON.parse(JSON.stringify(merged[k]))
      } else {
        const stack = [[props.params[k], merged[k]]]
        while (stack.length) {
          const [cur, def] = stack.pop()
          for (const [sk, sv] of Object.entries(def)) {
            if (cur[sk] === undefined || cur[sk] === null || cur[sk] === '') {
              cur[sk] = typeof sv === 'object' && sv !== null && !Array.isArray(sv)
                ? JSON.parse(JSON.stringify(sv))
                : sv
            } else if (typeof sv === 'object' && sv !== null && !Array.isArray(sv) && typeof cur[sk] === 'object') {
              stack.push([cur[sk], sv])
            }
          }
        }
      }
    } else if (props.params[k] === undefined || props.params[k] === null) {
      props.params[k] = merged[k]
    }
  })
  // 同步加仓两侧 VOL 开关/倍数（UI 共用 long 控件时写回 short）
  if (props.params.add_conditions?.level1) {
    props.params.add_conditions.level1.short.enable_vol = props.params.add_conditions.level1.long.enable_vol
    props.params.add_conditions.level1.short.vol_mult = props.params.add_conditions.level1.long.vol_mult
    props.params.add_conditions.level1.short.vol_lookback = props.params.add_conditions.level1.long.vol_lookback
  }
  if (props.params.add_conditions?.level2) {
    props.params.add_conditions.level2.short.enable_vol = props.params.add_conditions.level2.long.enable_vol
    props.params.add_conditions.level2.short.vol_mult = props.params.add_conditions.level2.long.vol_mult
    props.params.add_conditions.level2.short.vol_lookback = props.params.add_conditions.level2.long.vol_lookback
  }
  syncFromParams()
}

watch(volumeMinWan, (v) => {
  if (syncing || !props.params?.screening) return
  const wan = Number.isFinite(Number(v)) && Number(v) >= 0 ? Math.round(Number(v)) : DEFAULT_VOLUME_WAN
  if (v == null || v === '') volumeMinWan.value = DEFAULT_VOLUME_WAN
  props.params.screening.volume_min_usd = wan * WAN
})

watch(mcapMinWan, (v) => {
  if (syncing || !props.params?.screening) return
  const wan = Number.isFinite(Number(v)) && Number(v) >= 0 ? Math.round(Number(v)) : DEFAULT_MCAP_WAN
  if (v == null || v === '') mcapMinWan.value = DEFAULT_MCAP_WAN
  props.params.screening.mcap_min_usd = wan * WAN
})

watch(mcapMaxYi, (v) => {
  if (syncing || !props.params?.screening) return
  const yi = Number.isFinite(Number(v)) && Number(v) >= 0 ? Math.round(Number(v)) : DEFAULT_MCAP_MAX_YI
  if (v == null || v === '') mcapMaxYi.value = DEFAULT_MCAP_MAX_YI
  props.params.screening.mcap_max_usd = yi * YI
})

// 加仓 VOL 控件写回 short 侧
watch(() => props.params?.add_conditions?.level1?.long?.enable_vol, (v) => {
  if (props.params?.add_conditions?.level1?.short) props.params.add_conditions.level1.short.enable_vol = v
})
watch(() => props.params?.add_conditions?.level1?.long?.vol_mult, (v) => {
  if (props.params?.add_conditions?.level1?.short) props.params.add_conditions.level1.short.vol_mult = v
})
watch(() => props.params?.add_conditions?.level2?.long?.enable_vol, (v) => {
  if (props.params?.add_conditions?.level2?.short) props.params.add_conditions.level2.short.enable_vol = v
})
watch(() => props.params?.add_conditions?.level2?.long?.vol_mult, (v) => {
  if (props.params?.add_conditions?.level2?.short) props.params.add_conditions.level2.short.vol_mult = v
})

onMounted(ensureDefaults)
watch(() => props.params, ensureDefaults, { deep: true, immediate: true })
</script>

<style scoped>
.params-card { background: #131722; border-color: #232a39; margin-bottom: 12px; }
:deep(.params-card .el-card__header) {
  background: #161a23; border-bottom: 1px solid #232a39; padding: 10px 16px;
}
:deep(.el-card__header) { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
:deep(.el-card__header b) { color: #e2e6ee; font-size: 14px; }
:deep(.el-card__body) { padding: 14px; }
.hint { font-size: 11.5px; color: #6b7280; font-weight: 400; }
.block {
  background: #0f1219; border: 1px solid #1d2330; border-radius: 8px;
  padding: 12px 14px; margin-bottom: 10px;
}
.block:last-child { margin-bottom: 0; }
.block-title {
  font-size: 12px; color: #c4cad8; margin-bottom: 12px;
  font-weight: 700; letter-spacing: 0.3px;
}
.row { display: flex; flex-wrap: wrap; gap: 12px 16px; align-items: flex-end; }
.field { display: flex; flex-direction: column; gap: 4px; min-width: 130px; }
.field label { font-size: 11px; color: #8a93a7; font-weight: 500; }
.grid-3 { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px; }
.param-box {
  background: #0c0f16; border: 1px solid #1d2330; border-radius: 8px; padding: 10px 12px;
  display: flex; flex-direction: column; gap: 8px;
}
.param-head { display: flex; align-items: center; gap: 8px; color: #d6dae3; font-size: 12.5px; }
.unit { font-size: 11px; color: #6b7280; }
.tp-row {
  display: flex; flex-wrap: wrap; align-items: center; gap: 8px 10px;
  margin-bottom: 10px;
}
.tp-row:last-child { margin-bottom: 0; }
.hint-block { background: #0d1117; }
.strategy-hint { font-size: 12.5px; color: #b6bccb; line-height: 1.8; }
.strategy-hint p { margin: 2px 0; }
.loading { color: #8a93a7; padding: 12px; text-align: center; }
:deep(.el-checkbox__label) { color: #b6bccb; font-size: 12.5px; }
:deep(.el-input-number) { width: 120px; }
:deep(.el-input-number .el-input__wrapper) {
  background: #1a1f2c !important;
  box-shadow: 0 0 0 1px #2a3344 inset !important;
}
:deep(.el-input-number .el-input__inner) {
  color: #e8ecf4 !important;
  -webkit-text-fill-color: #e8ecf4 !important;
  font-weight: 600;
}
:deep(.el-select .el-select__wrapper) {
  background: #1a1f2c !important;
  box-shadow: 0 0 0 1px #2a3344 inset !important;
}
</style>
