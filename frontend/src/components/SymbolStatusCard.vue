<template>
  <el-card class="sym-card" shadow="never">
    <div class="head">
      <el-switch
        :model-value="cfg.enabled !== false"
        :loading="toggling"
        :disabled="toggling"
        @change="onToggle"
      />
      <div class="sym-info">
        <div class="sym-line">
          <span class="sym">{{ cfg.symbol }}</span>
          <el-tag size="small" :type="dirTag(runtime?.direction)" effect="dark">{{ runtime?.direction || 'FLAT' }}</el-tag>
          <el-tag v-if="cfg.enabled === false" type="warning" size="small" effect="plain">禁开仓</el-tag>
          <el-tag v-for="leg in runtime?.legs || []" :key="leg.direction" size="small"
                  :type="leg.direction === 'LONG' ? 'success' : 'danger'" effect="plain">
            {{ leg.direction }} · {{ leg.orders_count }}单
            <template v-if="leg.tp6_fired">· 保本损</template>
          </el-tag>
        </div>
        <div class="meta-line">
          <span v-if="runtime?.mark_price">mark <b>{{ fmt(runtime.mark_price, 4) }}</b></span>
          <span v-for="leg in runtime?.legs || []" :key="`e-${leg.direction}`">
            {{ leg.direction === 'LONG' ? '多' : '空' }}entry <b>{{ fmt(leg.entry_price, 4) }}</b>
          </span>
          <span :class="pnlClass">{{ formatPnl(runtime?.unrealized_pnl) }}</span>
        </div>
        <div class="diag-line" v-if="runtime?.entry_diag">
          <template v-for="(d, dir) in runtime.entry_diag" :key="dir">
            <span v-if="d" class="diag" :class="d.ok ? 'ok' : 'no'"
                  :title="`评估时间 ${d.ts}${d.first ? ' · 启动后首次仅记录' : ''}`">
              {{ dir === 'LONG' ? '多' : '空' }}{{ d.ok ? ' ✓可开' : ` ✗ ${d.reason}` }}{{ d.first ? ' ·首录' : '' }}
            </span>
          </template>
        </div>
      </div>
      <el-button v-if="runtime && runtime.direction !== 'FLAT'" type="danger" size="small" plain @click="$emit('emergency', cfg.symbol)">
        紧急平仓
      </el-button>
    </div>

    <div class="tf-grid">
      <div class="tf-cell" v-for="cell in tfCells" :key="cell.tf">
        <div class="tf-label">{{ cell.tf }}</div>
        <div class="tf-row"><span class="k">RSI</span><span :class="rsiClass(cell.ind?.rsi)">{{ fmt(cell.ind?.rsi, 1) }}</span></div>
        <div class="tf-row">
          <span class="k">EMA</span>
          <span :class="trendClass(cell.ind)">{{ trendText(cell.ind) }}</span>
        </div>
        <div class="tf-row" v-if="cell.tf === '5m'">
          <span class="k">MACD柱</span><span :class="histClass(cell.ind?.hist)">{{ fmt(cell.ind?.hist, 4) }}</span>
        </div>
        <div class="tf-row" v-if="cell.tf === '5m'">
          <span class="k">VOL×</span><span>{{ fmt(cell.ind?.volume_ratio, 2) }}</span>
        </div>
      </div>
    </div>
  </el-card>
</template>

<script setup>
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { configApi } from '../api'

const SHOW_TFS = ['5m', '15m', '30m', '1h', '2h', '1m']

const props = defineProps({
  cfg: { type: Object, required: true },
  runtime: { type: Object, default: null },
  timeframe: { type: String, default: '5m' },
})
const emit = defineEmits(['emergency', 'enabled-change'])

const toggling = ref(false)

const indicators = computed(() => props.runtime?.indicators || {})
const tfCells = computed(() =>
  SHOW_TFS
    .filter((tf) => indicators.value[tf])
    .map((tf) => ({ tf, ind: indicators.value[tf] })),
)

const pnlClass = computed(() => {
  const v = props.runtime?.unrealized_pnl
  if (!v || (props.runtime?.legs || []).length === 0) return 'pnl pnl-flat'
  return v >= 0 ? 'pnl up' : 'pnl down'
})

async function onToggle(enabled) {
  const prev = props.cfg.enabled !== false
  props.cfg.enabled = enabled
  toggling.value = true
  try {
    await configApi.setSymbolEnabled(props.cfg.symbol, enabled)
    emit('enabled-change', { symbol: props.cfg.symbol, enabled })
    ElMessage.success(enabled ? `${props.cfg.symbol} 已启用` : `${props.cfg.symbol} 已禁开仓（持仓仍止盈止损）`)
  } catch {
    props.cfg.enabled = prev
  } finally {
    toggling.value = false
  }
}

function fmt(v, digits = 2) {
  if (v == null || v === '' || Number.isNaN(Number(v))) return '—'
  return Number(v).toFixed(digits)
}

function formatPnl(v) {
  if (v == null) return ''
  const n = Number(v)
  const sign = n > 0 ? '+' : ''
  return `${sign}${n.toFixed(2)}`
}

function dirTag(d) {
  if (d === 'LONG') return 'success'
  if (d === 'SHORT') return 'danger'
  return 'info'
}

function rsiClass(v) {
  if (v == null) return ''
  if (v >= 70) return 'rsi hot'
  if (v <= 30) return 'rsi cold'
  return ''
}

function trendText(ind) {
  const e7 = ind?.ema7
  const e25 = ind?.ema25
  const e99 = ind?.ema99
  if (e7 == null || e25 == null || e99 == null) return '—'
  const cross = e7 > e25 ? '7>25' : '7<25'
  const vs99 = e7 > e99 && e25 > e99 ? '均>99' : (e7 < e99 && e25 < e99 ? '均<99' : '跨99')
  return `${cross} ${vs99}`
}

function trendClass(ind) {
  const e7 = ind?.ema7
  const e25 = ind?.ema25
  const e99 = ind?.ema99
  if (e7 == null || e25 == null || e99 == null) return ''
  if (e7 > e25 && e7 > e99 && e25 > e99) return 'trend up'
  if (e7 < e25 && e7 < e99 && e25 < e99) return 'trend down'
  return ''
}

function histClass(v) {
  if (v == null) return ''
  return v >= 0 ? 'rsi hot' : 'rsi cold'
}
</script>

<style scoped>
.sym-card { background: #131722; border-color: #232a39; margin-bottom: 10px; }
:deep(.el-card__body) { padding: 12px 14px; }
.head { display: flex; align-items: center; gap: 12px; margin-bottom: 10px; }
.sym-info { flex: 1; min-width: 0; }
.sym-line { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.sym { font-weight: 700; color: #e8ecf4; font-size: 14px; }
.meta-line { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 4px; font-size: 12px; color: #8a93a7; }
.meta-line b { color: #d6dae3; font-weight: 600; }
.diag-line { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 3px; font-size: 11px; }
.diag.ok { color: #3ecf8e; }
.diag.no { color: #98a1b3; }
.pnl { font-weight: 700; }
.pnl.up { color: #3ecf8e; }
.pnl.down { color: #f56565; }
.pnl-flat { color: #6b7280; }
.tf-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 8px; }
.tf-cell { background: #0c0f16; border: 1px solid #1d2330; border-radius: 6px; padding: 8px 10px; }
.tf-label { font-size: 11px; color: #8a93a7; margin-bottom: 6px; font-weight: 700; }
.tf-row { display: flex; justify-content: space-between; font-size: 12px; color: #c4cad8; margin: 2px 0; }
.tf-row .k { color: #6b7280; }
.rsi.hot { color: #f56565; }
.rsi.cold { color: #3ecf8e; }
.trend.up { color: #3ecf8e; }
.trend.down { color: #f56565; }
</style>
