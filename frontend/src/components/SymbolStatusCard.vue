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
          <el-tag v-if="(runtime?.add_count || 0) > 0" type="warning" size="small" effect="plain">加仓{{ runtime.add_count }}</el-tag>
          <el-tag v-if="runtime?.add_blocked" type="info" size="small" effect="plain">禁补仓</el-tag>
          <el-tag v-if="runtime?.pending_baseline" type="warning" size="small" effect="plain">等基准</el-tag>
          <el-tag v-if="runtime?.baseline_armed" type="success" size="small" effect="plain">基准已武装</el-tag>
        </div>
        <div class="meta-line">
          <span v-if="runtime?.mark_price">mark <b>{{ fmt(runtime.mark_price, 4) }}</b></span>
          <span v-if="runtime?.entry_price && runtime?.direction !== 'FLAT'">entry <b>{{ fmt(runtime.entry_price, 4) }}</b></span>
          <span v-if="runtime?.baseline_price">基准价 <b>{{ fmt(runtime.baseline_price, 4) }}</b></span>
          <span :class="pnlClass">{{ formatPnl(runtime?.unrealized_pnl) }}</span>
        </div>
      </div>
      <el-button v-if="runtime && runtime.direction !== 'FLAT'" type="danger" size="small" plain @click="$emit('emergency', cfg.symbol)">
        紧急平仓
      </el-button>
    </div>

    <div class="tf-grid">
      <div class="tf-cell">
        <div class="tf-label">{{ tfLabel }}</div>
        <div class="tf-row"><span class="k">RSI</span><span :class="rsiClass(ind?.rsi)">{{ fmt(ind?.rsi, 1) }}</span></div>
        <div class="tf-row"><span class="k">VOL×</span><span>{{ fmt(ind?.volume_ratio, 2) }}</span></div>
        <div class="tf-row"><span class="k">量均</span><span>{{ fmt(ind?.volume_avg, 2) }}</span></div>
        <div class="tf-row"><span class="k">现量</span><span>{{ fmt(ind?.volume, 2) }}</span></div>
      </div>
    </div>
  </el-card>
</template>

<script setup>
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { configApi } from '../api'

const props = defineProps({
  cfg: { type: Object, required: true },
  runtime: { type: Object, default: null },
  timeframe: { type: String, default: '1m' },
})
const emit = defineEmits(['emergency', 'enabled-change'])

const toggling = ref(false)

const tfLabel = computed(() => props.timeframe || '1m')
const ind = computed(() => {
  const indicators = props.runtime?.indicators || {}
  return indicators[tfLabel.value] || Object.values(indicators)[0] || null
})

const pnlClass = computed(() => {
  const v = props.runtime?.unrealized_pnl
  const dir = props.runtime?.direction
  if (!v || dir === 'FLAT') return 'pnl pnl-flat'
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
.pnl { font-weight: 700; }
.pnl.up { color: #3ecf8e; }
.pnl.down { color: #f56565; }
.pnl-flat { color: #6b7280; }
.tf-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 8px; }
.tf-cell { background: #0c0f16; border: 1px solid #1d2330; border-radius: 6px; padding: 8px 10px; }
.tf-label { font-size: 11px; color: #8a93a7; margin-bottom: 6px; font-weight: 700; }
.tf-row { display: flex; justify-content: space-between; font-size: 12px; color: #c4cad8; margin: 2px 0; }
.tf-row .k { color: #6b7280; }
.rsi.hot { color: #f56565; }
.rsi.cold { color: #3ecf8e; }
</style>
