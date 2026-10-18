<template>
  <el-card class="params-card" shadow="never">
    <template #header>
      <b>策略参数设置</b>
      <span class="hint">多周期 EMA/MACD/RSI · 双向持仓 · 修改后点上方「保存配置」</span>
    </template>

    <template v-if="ready">
      <section class="block">
        <div class="block-title">基础</div>
        <div class="row">
          <div class="field">
            <label>杠杆模式</label>
            <el-select v-model="params.leverage_mode" size="small" style="width:140px">
              <el-option label="跟随交易所" value="follow" />
              <el-option label="手动设置" value="manual" />
            </el-select>
          </div>
          <div class="field" v-if="params.leverage_mode === 'manual'">
            <label>手动杠杆</label>
            <el-select v-model="params.leverage" size="small" style="width:100px">
              <el-option label="5x" :value="5" />
              <el-option label="10x" :value="10" />
              <el-option label="20x" :value="20" />
            </el-select>
          </div>
          <div class="field slider-field">
            <label>每单保证金（USDT，1–100）</label>
            <el-slider v-model="params.order_plan.order_margin_usdt" :min="1" :max="100" :step="1" show-input size="small" />
            <div class="unit">名义 = 保证金 × 杠杆 · 首仓与各加仓档相同</div>
          </div>
        </div>
        <div class="unit" style="margin-top:8px">
          信号周期固定 5 分钟（收盘判定）；K 线订阅 1m/5m/15m/30m/1h/2h。
          开仓为边沿触发：启动后先记录条件状态，需由不满足变为满足才开单（防启动追单）。
        </div>
      </section>

      <section class="block">
        <div class="block-title">选币筛选（均可开关 + 滑杆调节）</div>
        <div class="grid-3">
          <div class="param-box">
            <div class="param-head">
              <el-switch v-model="params.screening.enable_mcap" />
              <span>市值下限</span>
            </div>
            <el-slider v-model="mcapMinWan" :min="0" :max="50000" :step="100" :show-input="true" size="small" :disabled="!params.screening.enable_mcap" />
            <div class="unit">单位：万美元 · 默认 1500</div>
          </div>
          <div class="param-box">
            <div class="param-head">
              <el-switch v-model="params.screening.enable_mcap" />
              <span>市值上限</span>
            </div>
            <el-slider v-model="mcapMaxYi" :min="0" :max="2000" :step="10" :show-input="true" size="small" :disabled="!params.screening.enable_mcap" />
            <div class="unit">单位：亿美元 · 默认 1000</div>
          </div>
          <div class="param-box">
            <div class="param-head">
              <el-switch v-model="params.screening.enable_price" />
              <span>币价上限 (USD)</span>
            </div>
            <el-slider v-model="params.screening.price_max_usd" :min="0" :max="100" :step="0.5" :show-input="true" size="small" :disabled="!params.screening.enable_price" />
            <div class="unit">默认 30 美元</div>
          </div>
          <div class="param-box">
            <div class="param-head">
              <el-switch v-model="params.screening.enable_volume" />
              <span>24h 成交额下限</span>
            </div>
            <el-slider v-model="volumeMinWan" :min="0" :max="100000" :step="100" :show-input="true" size="small" :disabled="!params.screening.enable_volume" />
            <div class="unit">单位：万美元 · 默认 2000</div>
          </div>
          <div class="param-box">
            <div class="param-head">
              <el-switch v-model="params.screening.enable_volume" />
              <span>24h 成交额上限</span>
            </div>
            <el-slider v-model="volumeMaxYi" :min="0" :max="1000" :step="5" :show-input="true" size="small" :disabled="!params.screening.enable_volume" />
            <div class="unit">单位：亿美元 · 默认 300</div>
          </div>
          <div class="param-box">
            <div class="param-head">
              <span>选币刷新间隔（小时）</span>
            </div>
            <el-input-number v-model="params.screening.refresh_hours" :min="0.5" :max="24" :step="0.5" size="small" controls-position="right" />
          </div>
        </div>
      </section>

      <section class="block">
        <div class="block-title">开仓条件（5m 收盘判定 · 已勾选项 AND · 全关禁该方向）</div>
        <div class="side-wrap">
          <div class="side-head">
            <el-checkbox v-model="params.entry.enable_long"><b>允许开多</b></el-checkbox>
          </div>
          <template v-if="params.entry.enable_long">
            <div class="tp-row">
              <el-checkbox v-model="params.entry.long.ema_5m">5m EMA7 &gt; EMA25</el-checkbox>
              <el-checkbox v-model="params.entry.long.ema_5m_99">5m EMA7&amp;25 均 &gt; EMA99</el-checkbox>
            </div>
            <div class="tp-row">
              <el-checkbox v-model="params.entry.long.rsi">5m RSI(6) &lt;</el-checkbox>
              <el-input-number v-model="params.entry.long.rsi_threshold" :min="1" :max="85" :precision="0" size="small" controls-position="right" :disabled="!params.entry.long.rsi" />
              <el-checkbox v-model="params.entry.long.macd_dif">5m DIF &gt; DEA</el-checkbox>
              <el-checkbox v-model="params.entry.long.macd_hist">5m MACD柱&lt;0连续3根缩小</el-checkbox>
            </div>
            <div class="tp-row">
              <el-checkbox v-model="params.entry.long.ema_15m">15m EMA7&gt;25 均&gt;99</el-checkbox>
              <el-checkbox v-model="params.entry.long.ema_30m">30m EMA7&gt;25 均&gt;99</el-checkbox>
              <el-checkbox v-model="params.entry.long.ema_60m">60m EMA7&gt;25 均&gt;99</el-checkbox>
              <el-checkbox v-model="params.entry.long.ema_120m">120m EMA7&gt;25 均&gt;99</el-checkbox>
            </div>
          </template>
        </div>
        <div class="side-wrap">
          <div class="side-head">
            <el-checkbox v-model="params.entry.enable_short"><b>允许开空</b></el-checkbox>
          </div>
          <template v-if="params.entry.enable_short">
            <div class="tp-row">
              <el-checkbox v-model="params.entry.short.ema_5m">5m EMA7 &lt; EMA25</el-checkbox>
              <el-checkbox v-model="params.entry.short.ema_5m_99">5m EMA7&amp;25 均 &lt; EMA99</el-checkbox>
            </div>
            <div class="tp-row">
              <el-checkbox v-model="params.entry.short.rsi">5m RSI(6) &gt;</el-checkbox>
              <el-input-number v-model="params.entry.short.rsi_threshold" :min="10" :max="99" :precision="0" size="small" controls-position="right" :disabled="!params.entry.short.rsi" />
              <el-checkbox v-model="params.entry.short.macd_dif">5m DIF &lt; DEA</el-checkbox>
              <el-checkbox v-model="params.entry.short.macd_hist">5m MACD柱&gt;0连续3根缩小</el-checkbox>
            </div>
            <div class="tp-row">
              <el-checkbox v-model="params.entry.short.ema_15m">15m EMA7&lt;25 均&lt;99</el-checkbox>
              <el-checkbox v-model="params.entry.short.ema_30m">30m EMA7&lt;25 均&lt;99</el-checkbox>
              <el-checkbox v-model="params.entry.short.ema_60m">60m EMA7&lt;25 均&lt;99</el-checkbox>
              <el-checkbox v-model="params.entry.short.ema_120m">120m EMA7&lt;25 均&lt;99</el-checkbox>
            </div>
          </template>
        </div>
      </section>

      <section class="block">
        <div class="block-title">下单计划（首仓 + 加仓档 · 每档仅一次 · 每档 1u）</div>
        <div class="tp-row">
          <el-checkbox v-model="params.order_plan.enable_first">满足开仓条件直接开首仓</el-checkbox>
        </div>
        <div class="tp-row">
          <el-checkbox v-model="params.order_plan.add_ema7">回踩 ≤5m EMA7（空: ≥）</el-checkbox>
          <el-checkbox v-model="params.order_plan.add_ema25">回踩 ≤5m EMA25 · 仅一次</el-checkbox>
          <el-checkbox v-model="params.order_plan.add_ema99">回踩 ≤5m EMA99 · 仅一次</el-checkbox>
        </div>
        <div class="tp-row">
          <el-checkbox v-model="params.order_plan.add_15m">15m 趋势满足加仓 · 仅一次</el-checkbox>
          <el-checkbox v-model="params.order_plan.add_30m">30m 趋势满足加仓 · 仅一次</el-checkbox>
        </div>
        <div class="unit" style="margin-top:6px">
          回踩判定：需先站上 EMA（多）/跌破 EMA（空）武装后，回踩触及才触发；各档最多 1 单，共 6 单。
        </div>
      </section>

      <section class="block">
        <div class="block-title">止盈条件（全部收盘判定 · 先到先执行 · 全平优先于 TP6 分批）</div>
        <div class="tp-row">
          <el-checkbox v-model="params.exit.enable_tp1">TP1 1m EMA25&lt;EMA99（空镜像）全平</el-checkbox>
        </div>
        <div class="tp-row">
          <el-checkbox v-model="params.exit.enable_tp2">TP2 5m EMA7&lt;EMA25（空镜像）全平</el-checkbox>
        </div>
        <div class="tp-row">
          <el-checkbox v-model="params.exit.enable_tp3">TP3 5m EMA25&lt;EMA99（空镜像）全平</el-checkbox>
        </div>
        <div class="tp-row">
          <el-checkbox v-model="params.exit.enable_tp4">TP4 15m EMA7&lt;EMA25（空镜像）全平</el-checkbox>
        </div>
        <div class="tp-row">
          <el-checkbox v-model="params.exit.enable_tp5">TP5 5m 放量 &gt; 前30根均量 ×</el-checkbox>
          <el-input-number v-model="params.exit.tp5_vol_mult" :min="5" :max="50" :precision="1" size="small" controls-position="right" :disabled="!params.exit.enable_tp5" />
          <span class="unit">全平（默认10倍，5–50可调）</span>
        </div>
        <div class="tp-row">
          <el-checkbox v-model="params.exit.enable_tp6"><b>TP6 多周期 RSI 分批</b></el-checkbox>
          <span class="unit">任一档触发后武装保本损（剩余仓位 mark 回到开仓均价全平）</span>
        </div>
        <template v-if="params.exit.enable_tp6">
          <div class="tp6-grid">
            <div class="tp6-col" v-for="side in tp6Sides" :key="side.key">
              <div class="side-head"><b>{{ side.label }}</b></div>
              <div class="tp6-row" v-for="lv in side.levels" :key="lv.key">
                <el-checkbox v-model="params.exit.tp6[side.key][lv.key].enabled">{{ lv.label }}</el-checkbox>
                <span class="unit">{{ side.op }}</span>
                <el-input-number v-model="params.exit.tp6[side.key][lv.key].rsi_threshold" :min="side.min" :max="side.max" :precision="0" size="small" controls-position="right" :disabled="!params.exit.tp6[side.key][lv.key].enabled" />
                <span class="unit">平</span>
                <el-input-number v-model="params.exit.tp6[side.key][lv.key].close_pct" :min="1" :max="100" :precision="0" size="small" controls-position="right" :disabled="!params.exit.tp6[side.key][lv.key].enabled" />
                <span class="unit">%</span>
              </div>
            </div>
          </div>
        </template>
      </section>

      <section class="block hint-block">
        <div class="strategy-hint">
          <p><b>开多（示例全部勾选）：</b>5m EMA多头排列 + RSI&lt;65 + MACD柱0轴下连续收缩 + 四个高周期多头排列 → 开多1u</p>
          <p><b>开空：</b>完全镜像（RSI&gt;50，柱0轴上连续收缩，高周期空头排列）</p>
          <p><b>加仓：</b>回踩 5m EMA7/25/99 各1单 + 15m/30m 趋势档各1单（各仅一次，双向持仓多空并存）</p>
          <p><b>止盈：</b>TP1–5 全平（也是止损）；TP6 分批后保本损盯开仓均价；旧仓按开仓时快照参数管理</p>
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
const DEFAULT_VOLUME_MIN_WAN = 2000
const DEFAULT_VOLUME_MAX_YI = 300
const DEFAULT_MCAP_WAN = 1500
const DEFAULT_MCAP_MAX_YI = 1000

const props = defineProps({
  params: { type: Object, required: true },
})

const ready = computed(() => !!(
  props.params
  && props.params.screening
  && props.params.entry
  && props.params.order_plan
  && props.params.exit
))

const volumeMinWan = ref(DEFAULT_VOLUME_MIN_WAN)
const volumeMaxYi = ref(DEFAULT_VOLUME_MAX_YI)
const mcapMinWan = ref(DEFAULT_MCAP_WAN)
const mcapMaxYi = ref(DEFAULT_MCAP_MAX_YI)
let syncing = false

const tp6Sides = [
  {
    key: 'long', label: '多单', op: '≥', min: 60, max: 99,
    levels: [
      { key: 't5m', label: '5m RSI' },
      { key: 't15m', label: '15m RSI' },
      { key: 't30m', label: '30m RSI' },
      { key: 't60m', label: '60m RSI' },
      { key: 't60m_120m', label: '60m&120m RSI' },
    ],
  },
  {
    key: 'short', label: '空单', op: '≤', min: 1, max: 30,
    levels: [
      { key: 't5m', label: '5m RSI' },
      { key: 't15m', label: '15m RSI' },
      { key: 't30m', label: '30m RSI' },
      { key: 't60m', label: '60m RSI' },
      { key: 't60m_120m', label: '60m&120m RSI' },
    ],
  },
]

function toWan(usd, fallback) {
  const n = Number(usd)
  return Number.isFinite(n) && n > 0 ? Math.round(n / WAN) : fallback
}
function toYi(usd, fallback) {
  const n = Number(usd)
  return Number.isFinite(n) && n > 0 ? Math.round(n / YI) : fallback
}

function syncFromParams() {
  if (!props.params?.screening) return
  syncing = true
  volumeMinWan.value = toWan(props.params.screening.volume_min_usd, DEFAULT_VOLUME_MIN_WAN)
  volumeMaxYi.value = toYi(props.params.screening.volume_max_usd, DEFAULT_VOLUME_MAX_YI)
  mcapMinWan.value = toWan(props.params.screening.mcap_min_usd, DEFAULT_MCAP_WAN)
  mcapMaxYi.value = toYi(props.params.screening.mcap_max_usd, DEFAULT_MCAP_MAX_YI)
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
  syncFromParams()
}

watch(volumeMinWan, (v) => {
  if (syncing || !props.params?.screening) return
  const wan = Number.isFinite(Number(v)) && Number(v) >= 0 ? Math.round(Number(v)) : DEFAULT_VOLUME_MIN_WAN
  props.params.screening.volume_min_usd = wan * WAN
})
watch(volumeMaxYi, (v) => {
  if (syncing || !props.params?.screening) return
  const yi = Number.isFinite(Number(v)) && Number(v) >= 0 ? Math.round(Number(v)) : DEFAULT_VOLUME_MAX_YI
  props.params.screening.volume_max_usd = yi * YI
})
watch(mcapMinWan, (v) => {
  if (syncing || !props.params?.screening) return
  const wan = Number.isFinite(Number(v)) && Number(v) >= 0 ? Math.round(Number(v)) : DEFAULT_MCAP_WAN
  props.params.screening.mcap_min_usd = wan * WAN
})
watch(mcapMaxYi, (v) => {
  if (syncing || !props.params?.screening) return
  const yi = Number.isFinite(Number(v)) && Number(v) >= 0 ? Math.round(Number(v)) : DEFAULT_MCAP_MAX_YI
  props.params.screening.mcap_max_usd = yi * YI
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
.slider-field { min-width: 320px; flex: 1; }
.grid-3 { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 10px; }
.param-box {
  background: #0c0f16; border: 1px solid #1d2330; border-radius: 8px; padding: 10px 12px;
  display: flex; flex-direction: column; gap: 8px;
}
.param-head { display: flex; align-items: center; gap: 8px; color: #d6dae3; font-size: 12.5px; }
.unit { font-size: 11px; color: #6b7280; }
.side-wrap {
  background: #0c0f16; border: 1px solid #1d2330; border-radius: 8px;
  padding: 8px 12px; margin-bottom: 8px;
}
.side-head { margin-bottom: 6px; color: #d6dae3; font-size: 12.5px; }
.tp-row {
  display: flex; flex-wrap: wrap; align-items: center; gap: 8px 10px;
  margin-bottom: 8px;
}
.tp-row:last-child { margin-bottom: 0; }
.tp6-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 10px; }
.tp6-col {
  background: #0c0f16; border: 1px solid #1d2330; border-radius: 8px; padding: 8px 12px;
}
.tp6-row {
  display: flex; flex-wrap: wrap; align-items: center; gap: 4px 6px; margin-bottom: 6px;
}
.tp6-row:last-child { margin-bottom: 0; }
.hint-block { background: #0d1117; }
.strategy-hint { font-size: 12.5px; color: #b6bccb; line-height: 1.8; }
.strategy-hint p { margin: 2px 0; }
.loading { color: #8a93a7; padding: 12px; text-align: center; }
:deep(.el-checkbox__label) { color: #b6bccb; font-size: 12.5px; }
:deep(.el-input-number) { width: 110px; }
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
:deep(.el-slider__runway) { background: #1a1f2c; }
</style>
