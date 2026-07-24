<template>
  <div class="dashboard">
    <div class="topbar">
      <div class="logo">⚡ RSI+成交量策略</div>
      <div class="status-pill" :class="status.running ? 'on' : 'off'">
        <span class="dot" />
        <span>{{ statusText }}</span>
      </div>
      <span class="spacer" />
      <span class="user">{{ auth.username }}</span>
      <el-button text @click="logout">退出登录</el-button>
    </div>

    <div class="control-bar">
      <el-button type="success" :loading="starting" :disabled="status.running || status.booting" size="large" @click="onStart">
        ▶ 启动策略
      </el-button>
      <el-button type="danger" :loading="stopping" :disabled="!status.running && !status.booting" size="large" @click="onStop">
        ■ 停止策略
      </el-button>
      <el-button
        type="warning"
        :loading="closingAll"
        :disabled="!status.running || openPositionCount === 0"
        size="large"
        @click="closeAllPositions"
      >
        一键平仓{{ openPositionCount > 0 ? ` (${openPositionCount})` : '' }}
      </el-button>
      <el-tooltip :disabled="!status.running" content="运行中请先停止策略再改参数并保存" placement="bottom">
        <span>
          <el-button type="primary" :loading="saving" :disabled="status.running || status.booting" size="large" @click="saveConfig">
            💾 保存配置
          </el-button>
        </span>
      </el-tooltip>
      <span v-if="status.booting" class="lock-tip">⏳ 正在选币与初始化指标，请稍候…</span>
      <span v-else-if="status.running" class="lock-tip">🔒 运行中，参数已锁定</span>
      <el-tag v-if="runtime.screener?.symbol_count" type="info" effect="plain" class="status-tag">
        监控 {{ runtime.screener.symbol_count }} 币
      </el-tag>
      <el-tag v-if="runtime.timeframe" type="info" effect="plain" class="status-tag">
        周期 {{ runtime.timeframe }}
      </el-tag>
      <span class="spacer" />
      <span class="meta" v-if="status.started_at">启动于 {{ formatTime(status.started_at) }}</span>
      <span class="update-pill" :class="updatePillClass">
        <span class="dot" />
        <span>数据更新 {{ lastUpdateText }}</span>
      </span>
    </div>

    <el-card class="global-card" shadow="never">
      <template #header>
        <b>全局参数</b>
        <el-tag size="small" :type="exchange.has_api_key && exchange.has_api_secret ? 'success' : 'info'" effect="plain">
          {{ exchange.has_api_key && exchange.has_api_secret ? 'API 已配置' : 'API 未配置' }}
        </el-tag>
        <el-tag size="small" :type="exchange.paper_trading ? 'warning' : 'danger'" effect="dark">
          {{ exchange.paper_trading ? '模拟盘' : '实盘' }}
        </el-tag>
        <span v-if="accountBalance.available !== null" class="balance-pill">
          账户可用：<b>{{ accountBalance.available.toFixed(2) }} USDT</b>
        </span>
        <span v-else-if="accountBalance.error" class="balance-pill err">{{ accountBalance.error }}</span>
        <el-button size="small" :loading="loadingBalance" plain @click="fetchBalance">
          {{ accountBalance.available !== null ? '🔄 刷新余额' : '查询账户余额' }}
        </el-button>
      </template>
      <div class="global-grid">
        <div class="field field-select">
          <label>本金来源</label>
          <el-select v-model="config.global.capital_source" size="small" :disabled="status.running">
            <el-option value="env" label="配置固定金额" />
            <el-option value="account" label="实时账户余额" />
          </el-select>
        </div>
        <div class="field">
          <label>本金 (USDT)</label>
          <el-input-number
            v-model="config.global.capital_usdt"
            :min="0"
            :precision="2"
            size="small"
            controls-position="right"
            :disabled="config.global.capital_source === 'account' || status.running"
          />
        </div>
      </div>

      <div class="api-section">
        <div class="api-title">币安合约 API</div>
        <div class="api-grid">
          <div class="field field-wide">
            <label>API Key</label>
            <el-input
              v-model="exchangeForm.api_key"
              size="small"
              clearable
              :disabled="status.running || status.booting"
              :placeholder="exchange.api_key_masked || '请输入 API Key'"
              autocomplete="off"
            />
          </div>
          <div class="field field-wide">
            <label>API Secret</label>
            <el-input
              v-model="exchangeForm.api_secret"
              size="small"
              show-password
              clearable
              :disabled="status.running || status.booting"
              :placeholder="exchange.has_api_secret ? '已保存，留空表示不修改' : '请输入 API Secret'"
              autocomplete="new-password"
            />
          </div>
          <div class="field field-switch">
            <label>模拟盘</label>
            <el-switch
              v-model="exchangeForm.paper_trading"
              :disabled="status.running || status.booting"
              active-text="模拟"
              inactive-text="实盘"
            />
          </div>
          <div class="field field-switch">
            <label>测试网</label>
            <el-switch
              v-model="exchangeForm.testnet"
              :disabled="status.running || status.booting"
              active-text="测试网"
              inactive-text="正式网"
            />
          </div>
          <div class="field field-actions">
            <el-button
              type="primary"
              size="small"
              :loading="savingExchange"
              :disabled="status.running || status.booting"
              @click="saveExchange"
            >
              保存 API
            </el-button>
            <el-button
              size="small"
              plain
              type="danger"
              :disabled="status.running || status.booting || (!exchange.has_api_key && !exchange.has_api_secret)"
              @click="clearExchange"
            >
              清除密钥
            </el-button>
          </div>
        </div>
        <div class="api-hint">
          密钥保存在服务器本地数据库；Secret 不会回显。运行中不可修改。关闭「模拟盘」后将真实下单，请谨慎。
        </div>
      </div>
    </el-card>

    <!-- 策略参数放在币种列表之前，方便设置 -->
    <StrategyParamsCard v-if="config.strategy" :params="config.strategy" />

    <el-row :gutter="16" class="logs-row">
      <el-col :span="14">
        <el-card class="log-card" shadow="never">
          <template #header><b>实时事件流</b><span class="hint">最近 200 条</span></template>
          <div class="logs">
            <div v-for="(e, i) in events" :key="i" class="log-row" :class="logClass(e)">
              <span class="ts">{{ formatTime(e.ts) }}</span>
              <span class="badge" :class="logClass(e)">{{ badgeText(e) }}</span>
              <span class="sym">{{ e.symbol || '' }}</span>
              <span class="msg">{{ messageOf(e) }}</span>
            </div>
            <div v-if="events.length === 0" class="empty">尚无事件</div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="10">
        <el-card class="log-card" shadow="never">
          <template #header>
            <b>成交记录</b>
            <span class="hint">最近 {{ trades.length }} 条</span>
          </template>
          <div class="logs">
            <div v-for="(t, i) in trades" :key="t.id || i" class="log-row" :class="tradeClass(t)">
              <span class="ts">{{ formatTime(t.ts) }}</span>
              <span class="badge" :class="tradeClass(t)">{{ tradeEventLabel(t.event) }}</span>
              <span class="sym">{{ t.symbol }} {{ sideLabel(t.position_side) }}</span>
              <span class="msg">{{ tradeMessage(t) }}</span>
            </div>
            <div v-if="trades.length === 0" class="empty">尚无成交</div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-card class="symbols-panel" shadow="never">
      <template #header>
        <b>监控币种</b>
        <span class="hint">共 {{ displaySymbols.length }} 个</span>
        <el-input
          v-model="symbolFilter"
          size="small"
          clearable
          placeholder="搜索币种"
          class="search-input"
        />
        <el-checkbox v-model="onlyHolding" class="only-hold">仅看持仓</el-checkbox>
        <el-button size="small" text @click="showAllSymbols = !showAllSymbols">
          {{ showAllSymbols ? '收起' : `展开全部 (当前显示 ${visibleSymbols.length})` }}
        </el-button>
      </template>

      <el-row :gutter="12" class="symbols-row">
        <el-col v-for="sym in visibleSymbols" :key="sym.symbol" :xs="24" :sm="12" :lg="8">
          <SymbolStatusCard
            :cfg="sym"
            :runtime="runtimeBySymbol[sym.symbol]"
            :timeframe="config.strategy?.timeframe || runtime.timeframe || '1m'"
            @emergency="emergencyClose"
            @enabled-change="onSymbolEnabled"
          />
        </el-col>
      </el-row>
      <div v-if="displaySymbols.length === 0" class="empty-symbols">
        <template v-if="symbolFilter || onlyHolding">
          当前筛选无结果（搜索「{{ symbolFilter || '全部' }}」{{ onlyHolding ? ' · 仅看持仓' : '' }}）
        </template>
        <template v-else>
          尚无监控币种 — 先设置参数并保存，再点「启动策略」
        </template>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, reactive, ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import SymbolStatusCard from '../components/SymbolStatusCard.vue'
import StrategyParamsCard from '../components/StrategyParamsCard.vue'
import { configApi, controlApi, openStream } from '../api'
import { useAuthStore } from '../store/auth'
import { mergeStrategyParams } from '../utils/strategyDefaults'

const router = useRouter()
const auth = useAuthStore()

const status = reactive({ running: false, booting: false, started_at: null })
const config = reactive({
  global: { capital_source: 'env', capital_usdt: 200 },
  strategy: mergeStrategyParams(null),
  symbols: [],
})
const runtime = ref({ positions: [], screener: {}, timeframe: '1m' })
const lastUpdate = ref(null)
const lastUpdateText = ref('—')
let updateTimer = null
const events = ref([])
const trades = ref([])
const starting = ref(false)
const stopping = ref(false)
const closingAll = ref(false)
const saving = ref(false)
const loadingBalance = ref(false)
const savingExchange = ref(false)
const accountBalance = reactive({ available: null, error: null })
const exchange = reactive({
  api_key_masked: '',
  has_api_key: false,
  has_api_secret: false,
  paper_trading: true,
  testnet: false,
})
const exchangeForm = reactive({
  api_key: '',
  api_secret: '',
  paper_trading: true,
  testnet: false,
})
const symbolFilter = ref('')
const onlyHolding = ref(false)
const showAllSymbols = ref(false)
let ws = null

const statusText = computed(() => {
  if (status.booting) return '启动中'
  return status.running ? '运行中' : '已停止'
})

const openPositionCount = computed(() =>
  (runtime.value?.positions || []).filter((p) => p.direction && p.direction !== 'FLAT').length,
)

const runtimeBySymbol = computed(() => {
  const m = {}
  for (const p of (runtime.value?.positions || [])) m[p.symbol] = p
  return m
})

const displaySymbols = computed(() => {
  const fromRuntime = (runtime.value?.positions || []).map(p => ({
    symbol: p.symbol,
    enabled: p.enabled !== false,
  }))
  const seen = new Set(fromRuntime.map(s => s.symbol))
  for (const s of (config.symbols || [])) {
    if (!seen.has(s.symbol)) fromRuntime.push(s)
  }
  let list = fromRuntime
  const q = (symbolFilter.value || '').trim().toUpperCase()
  if (q) list = list.filter(s => s.symbol.includes(q))
  if (onlyHolding.value) {
    list = list.filter(s => {
      const r = runtimeBySymbol.value[s.symbol]
      return r && r.direction && r.direction !== 'FLAT'
    })
  }
  return list
})

const visibleSymbols = computed(() => {
  if (showAllSymbols.value || onlyHolding.value || symbolFilter.value) return displaySymbols.value
  return displaySymbols.value.slice(0, 12)
})

onMounted(async () => {
  await loadConfig()
  await loadExchange()
  await refreshStatus()
  await refreshRuntime()
  await loadTrades()
  await loadEvents()
  connectWs()
  updateTimer = setInterval(() => {
    if (!lastUpdate.value) {
      lastUpdateText.value = '—'
      return
    }
    const sec = Math.floor((Date.now() - lastUpdate.value) / 1000)
    lastUpdateText.value = sec < 2 ? '刚刚' : `${sec} 秒前`
  }, 1000)
})

onBeforeUnmount(() => {
  if (ws) ws.close()
  if (updateTimer) clearInterval(updateTimer)
})

const updatePillClass = computed(() => {
  if (!lastUpdate.value) return 'stale'
  const sec = (Date.now() - lastUpdate.value) / 1000
  if (sec > 15) return 'stale'
  return 'live'
})

async function loadConfig() {
  try {
    const data = await configApi.get()
    config.global = data.global || { capital_source: 'env', capital_usdt: 200 }
    config.strategy = mergeStrategyParams(data.strategy)
    config.symbols = data.symbols || []
    syncSymbolsFromRuntime()
  } catch (e) {
    config.strategy = mergeStrategyParams(null)
  }
}

function applyExchangeView(data) {
  exchange.api_key_masked = data.api_key_masked || ''
  exchange.has_api_key = !!data.has_api_key
  exchange.has_api_secret = !!data.has_api_secret
  exchange.paper_trading = data.paper_trading !== false
  exchange.testnet = !!data.testnet
  exchangeForm.paper_trading = exchange.paper_trading
  exchangeForm.testnet = exchange.testnet
  // 不回填明文密钥
  exchangeForm.api_key = ''
  exchangeForm.api_secret = ''
}

async function loadExchange() {
  try {
    const data = await configApi.getExchange()
    applyExchangeView(data)
  } catch {}
}

async function saveExchange() {
  if (!exchangeForm.paper_trading) {
    await ElMessageBox.confirm(
      '即将关闭模拟盘，保存后下次启动将真实下单。确认继续？',
      '实盘确认',
      { type: 'warning' },
    )
  }
  savingExchange.value = true
  try {
    const payload = {
      paper_trading: exchangeForm.paper_trading,
      testnet: exchangeForm.testnet,
    }
    if (exchangeForm.api_key.trim()) payload.api_key = exchangeForm.api_key.trim()
    if (exchangeForm.api_secret.trim()) payload.api_secret = exchangeForm.api_secret.trim()
    const data = await configApi.updateExchange(payload)
    applyExchangeView(data)
    ElMessage.success('API 配置已保存')
  } catch (e) {
    // interceptor 已提示
  } finally {
    savingExchange.value = false
  }
}

async function clearExchange() {
  await ElMessageBox.confirm('确认清除已保存的 API Key / Secret？', '清除密钥', { type: 'warning' })
  savingExchange.value = true
  try {
    const data = await configApi.updateExchange({ clear_credentials: true })
    applyExchangeView(data)
    ElMessage.success('密钥已清除')
  } finally {
    savingExchange.value = false
  }
}

function syncSymbolsFromRuntime() {
  const seen = new Set((config.symbols || []).map(s => s.symbol))
  for (const p of (runtime.value?.positions || [])) {
    const existing = (config.symbols || []).find(s => s.symbol === p.symbol)
    if (existing) {
      existing.enabled = p.enabled !== false
    } else if (!seen.has(p.symbol)) {
      config.symbols.push({ symbol: p.symbol, enabled: p.enabled !== false })
      seen.add(p.symbol)
    }
  }
}

function onSymbolEnabled({ symbol, enabled }) {
  let row = (config.symbols || []).find(s => s.symbol === symbol)
  if (!row) {
    config.symbols.push({ symbol, enabled })
  } else {
    row.enabled = enabled
  }
  const p = (runtime.value?.positions || []).find(x => x.symbol === symbol)
  if (p) p.enabled = enabled
}

async function refreshStatus() {
  try {
    const s = await controlApi.status()
    Object.assign(status, {
      running: !!s.running,
      booting: !!s.booting,
      started_at: s.started_at || null,
    })
  } catch {}
}

async function refreshRuntime() {
  try {
    runtime.value = await controlApi.runtime()
    if (runtime.value?.booting != null) status.booting = !!runtime.value.booting
    if (runtime.value?.running != null) status.running = !!runtime.value.running
    syncSymbolsFromRuntime()
  } catch {}
}

async function loadTrades() {
  try {
    const data = await controlApi.trades(200)
    const items = Array.isArray(data?.items) ? data.items : []
    trades.value = items
  } catch {}
}

async function loadEvents() {
  try {
    const data = await controlApi.logs(200)
    const items = Array.isArray(data?.items) ? data.items : []
    // 与成交合并进事件流：日志在前（已按新到旧）
    const merged = []
    const seen = new Set()
    for (const e of items) {
      const key = e.id != null ? `log-${e.id}` : `${e.ts}-${e.message}`
      if (seen.has(key)) continue
      seen.add(key)
      merged.push(e)
    }
    for (const t of trades.value) {
      const key = t.id != null ? `trade-${t.id}` : `${t.ts}-${t.symbol}-${t.event}`
      if (seen.has(key)) continue
      seen.add(key)
      merged.push({ ...t, type: 'trade', _category: 'trade' })
    }
    merged.sort((a, b) => String(b.ts || '').localeCompare(String(a.ts || '')))
    events.value = merged.slice(0, 200)
  } catch {}
}

function pushTrade(event) {
  if (event?.id != null && trades.value.some((t) => t.id === event.id)) return
  trades.value.unshift(event)
  if (trades.value.length > 200) trades.value.length = 200
}

async function onStart() {
  await ElMessageBox.confirm(
    '将启动多周期策略：后台自动选币并初始化指标（约几十秒）。确认？',
    '启动确认',
    { type: 'info' },
  )
  starting.value = true
  try {
    await controlApi.start()
    ElMessage.success('已开始启动，正在后台选币…')
    status.running = true
    status.booting = true
    await refreshStatus()
    await refreshRuntime()
    await loadConfig()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || e.message || '启动失败')
  } finally {
    starting.value = false
  }
}

async function onStop() {
  await ElMessageBox.confirm('停止后会断开行情连接。继续？', '停止确认', { type: 'warning' })
  stopping.value = true
  try {
    await controlApi.stop()
    ElMessage.success('已停止')
    status.running = false
    status.booting = false
    await refreshStatus()
  } finally {
    stopping.value = false
  }
}

async function fetchBalance() {
  loadingBalance.value = true
  try {
    const r = await controlApi.balance()
    accountBalance.available = r.available
    accountBalance.error = r.error
    if (r.error) ElMessage.warning(r.error)
    else if (r.available !== null) ElMessage.success(`账户可用余额：${r.available.toFixed(2)} USDT`)
  } finally {
    loadingBalance.value = false
  }
}

async function saveConfig() {
  saving.value = true
  try {
    const payload = {
      global: config.global,
      strategy: mergeStrategyParams(config.strategy),
      symbols: config.symbols,
    }
    const saved = await configApi.update(payload)
    config.strategy = mergeStrategyParams(saved.strategy)
    config.symbols = saved.symbols || config.symbols
    ElMessage.success('配置已保存')
  } finally {
    saving.value = false
  }
}

async function emergencyClose(symbol) {
  await ElMessageBox.confirm(`确认紧急平掉 ${symbol} 的全部持仓？`, '紧急平仓', { type: 'warning' })
  await controlApi.emergencyClose(symbol)
  ElMessage.success(`${symbol} 已发送平仓指令`)
}

async function closeAllPositions() {
  const n = openPositionCount.value
  if (n <= 0) {
    ElMessage.info('当前无持仓')
    return
  }
  await ElMessageBox.confirm(
    `确认一键平掉全部 ${n} 个持仓？此操作不可撤销。`,
    '一键平仓',
    { type: 'warning', confirmButtonText: '全部平仓', cancelButtonText: '取消' },
  )
  closingAll.value = true
  try {
    const r = await controlApi.closeAll()
    const closed = r?.count ?? r?.closed?.length ?? 0
    if (r?.errors?.length) {
      ElMessage.warning(`已平 ${closed} 个，失败 ${r.errors.length} 个`)
    } else {
      ElMessage.success(`已平仓 ${closed} 个`)
    }
    await refreshRuntime()
  } finally {
    closingAll.value = false
  }
}

function logout() {
  auth.logout()
  router.push({ name: 'login' })
}

function connectWs() {
  if (!auth.token) return
  ws = openStream(auth.token, (event) => {
    lastUpdate.value = Date.now()

    if (event.type === 'engine.status') {
      status.running = !!event.running
      status.booting = !!event.booting
      status.started_at = event.started_at || null
    } else if (event.type === 'runtime') {
      runtime.value = event.data || {}
      status.running = event.data?.running ?? status.running
      status.booting = event.data?.booting ?? status.booting
      syncSymbolsFromRuntime()
    } else if (event.type === 'trade') {
      pushTrade(event)
      const key = event.id != null ? `trade-${event.id}` : null
      if (!key || !events.value.some((e) => e.id === event.id && e.type === 'trade')) {
        events.value.unshift({ ...event, _category: 'trade' })
      }
    } else if (event.type === 'log') {
      if (event.id != null && events.value.some((e) => e.id === event.id && e.type === 'log')) {
        // skip dup
      } else {
        events.value.unshift(event)
      }
    } else if (event.type === 'indicator') {
      return
    }
    if (events.value.length > 200) events.value.length = 200
  })
}

function formatTime(t) {
  if (!t) return ''
  try {
    let s = String(t).trim()
    // 后端曾发无时区的 UTC（utcnow），补 Z；已带 +08:00 / Z 的原样解析
    if (!/[zZ]|[+-]\d{2}:?\d{2}$/.test(s)) {
      s = s.includes('T') ? `${s}Z` : s
    }
    return new Date(s).toLocaleString('zh-CN', {
      hour12: false,
      timeZone: 'Asia/Shanghai',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    })
  } catch {
    return String(t)
  }
}
function logClass(e) {
  if (e.type === 'trade') return e.event && (e.event.startsWith('CLOSE') ? 'ok' : 'warn')
  if (e.level === 'ERROR') return 'err'
  if (e.level === 'WARN') return 'warn'
  return ''
}
function tradeClass(t) {
  if (t.event && t.event.startsWith('CLOSE')) return 'ok'
  return 'warn'
}

const TRADE_EVENT_LABELS = {
  OPEN: '开仓',
  ADD: '加仓',
  CLOSE_TP: '止盈平仓',
  CLOSE_TP1: '止盈1回撤',
  CLOSE_TP2: '止盈2 RSI',
  CLOSE_SL: '止损平仓',
  CLOSE_SL1: '止损1保本',
  CLOSE_SL2: '止损2浮亏',
  CLOSE_REVERSE: '反手平仓',
  CLOSE_MANUAL: '手动平仓',
  CLOSE_EXTERNAL: '外部平仓/强平',
}

function tradeEventLabel(event) {
  if (!event) return '成交'
  return TRADE_EVENT_LABELS[event] || event
}

function sideLabel(side) {
  if (side === 'LONG') return '多'
  if (side === 'SHORT') return '空'
  return side || ''
}

function tradeMessage(t) {
  const qty = Number(t.quantity)
  const price = Number(t.price)
  const pnl = Number(t.realized_pnl || 0)
  const qtyText = Number.isFinite(qty) ? qty : t.quantity
  const priceText = Number.isFinite(price) ? price.toFixed(4) : t.price
  const pnlText = Number.isFinite(pnl) ? pnl.toFixed(2) : '0.00'
  const isClose = t.event && String(t.event).startsWith('CLOSE')
  if (isClose) {
    return `数量 ${qtyText} @ ${priceText}  盈亏 ${pnlText}`
  }
  return `数量 ${qtyText} @ ${priceText}`
}

function badgeText(e) {
  if (e.type === 'trade') return tradeEventLabel(e.event)
  const levelMap = { INFO: '信息', WARN: '警告', ERROR: '错误' }
  return levelMap[e.level] || e.level || '信息'
}
function messageOf(e) {
  if (e.type === 'trade') {
    const side = sideLabel(e.position_side)
    return `${side ? side + ' · ' : ''}${tradeMessage(e)}`
  }
  return e.message || ''
}
</script>

<style scoped>
.dashboard {
  padding: 20px 32px 40px;
  min-height: 100vh;
  max-width: 1600px;
  margin: 0 auto;
  box-sizing: border-box;
}
.topbar {
  display: flex; align-items: center; gap: 12px;
  padding: 8px 12px; background: #131722; border: 1px solid #232a39;
  border-radius: 10px; margin-bottom: 12px;
}
.logo { font-weight: 700; font-size: 18px; color: #e2e6ee; }
.user { color: #b6bccb; }
.spacer { flex: 1; }
.status-pill { display: flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 999px; font-size: 12px; }
.status-pill .dot { width: 8px; height: 8px; border-radius: 50%; }
.status-pill.on { background: rgba(37, 198, 133, .12); color: #25c685; }
.status-pill.on .dot { background: #25c685; box-shadow: 0 0 6px #25c685; }
.status-pill.off { background: rgba(255, 93, 93, .12); color: #ff5d5d; }
.status-pill.off .dot { background: #ff5d5d; }

.control-bar {
  display: flex; align-items: center; gap: 10px; margin-bottom: 12px; flex-wrap: wrap;
  padding: 12px; background: #131722; border: 1px solid #232a39; border-radius: 10px;
}
.meta { color: #8a93a7; font-size: 12px; }
.lock-tip {
  font-size: 12px;
  color: #f5a623;
  background: rgba(245, 166, 35, 0.12);
  padding: 4px 10px;
  border-radius: 999px;
  margin-left: 4px;
}
.status-tag { margin-left: 4px; }

.control-bar :deep(.el-button.is-disabled) {
  background: #1a1f2c !important;
  border-color: #232a39 !important;
  color: #4a5163 !important;
  opacity: 1 !important;
}
.update-pill {
  display: inline-flex; align-items: center; gap: 6px;
  font-size: 12px; padding: 4px 10px; border-radius: 999px;
}
.update-pill .dot { width: 6px; height: 6px; border-radius: 50%; }
.update-pill.live { background: rgba(37, 198, 133, .12); color: #25c685; }
.update-pill.live .dot { background: #25c685; box-shadow: 0 0 6px #25c685; }
.update-pill.stale { background: rgba(255, 93, 93, .12); color: #ff5d5d; }
.update-pill.stale .dot { background: #ff5d5d; }

.global-card, .log-card, .symbols-panel { background: #131722; border-color: #232a39; margin-bottom: 12px; }
.logs-row { margin-bottom: 4px; }
:deep(.global-card .el-card__header),
:deep(.log-card .el-card__header),
:deep(.symbols-panel .el-card__header) {
  background: #161a23; border-bottom: 1px solid #232a39; padding: 10px 16px;
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
}
:deep(.global-card .el-card__header b),
:deep(.log-card .el-card__header b),
:deep(.symbols-panel .el-card__header b) { color: #e2e6ee; font-size: 14px; }
.balance-pill {
  font-size: 12px;
  background: rgba(37, 198, 133, 0.12);
  color: #5fdcaa;
  padding: 4px 10px;
  border-radius: 999px;
}
.balance-pill b { color: #25c685; margin-left: 4px; }
.balance-pill.err { background: rgba(255, 93, 93, 0.12); color: #ff8585; }
:deep(.el-card__body) { padding: 16px; }

.global-grid { display: flex; flex-wrap: wrap; gap: 12px 18px; }
.global-grid .field { display: flex; flex-direction: column; gap: 6px; width: 130px; }
.global-grid .field-select { width: 160px; }
.global-grid .field label { font-size: 11.5px; color: #8a93a7; }
.global-grid .field :deep(.el-input-number),
.global-grid .field :deep(.el-select) { width: 100%; }
.api-section {
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px solid #232a39;
}
.api-title {
  font-size: 13px;
  font-weight: 600;
  color: #d7dce8;
  margin-bottom: 10px;
}
.api-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 12px 16px;
  align-items: flex-end;
}
.api-grid .field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.api-grid .field label { font-size: 11.5px; color: #8a93a7; }
.api-grid .field-wide { width: min(360px, 100%); }
.api-grid .field-switch { width: 140px; }
.api-grid .field-actions {
  flex-direction: row;
  align-items: center;
  gap: 8px;
  padding-bottom: 2px;
}
.api-hint {
  margin-top: 10px;
  font-size: 12px;
  color: #6b7280;
  line-height: 1.5;
}

.hint { margin-left: 8px; color: #8a93a7; font-size: 12px; font-weight: 400; }
.search-input { width: 160px; margin-left: auto; }
.only-hold { margin-left: 8px; color: #b6bccb; }

.symbols-row { margin-bottom: 0; }
.symbols-row > .el-col { margin-bottom: 12px; }
.empty-symbols { padding: 16px; text-align: center; color: #6b7280; }

.logs { max-height: 480px; overflow-y: auto; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
.log-row { display: flex; gap: 8px; padding: 4px 8px; border-bottom: 1px solid #1d2330; }
.log-row .ts { color: #6b7280; flex-shrink: 0; }
.log-row .badge { padding: 0 6px; border-radius: 4px; flex-shrink: 0; background: #2c3344; color: #b6bccb; }
.log-row.ok .badge { background: rgba(37,198,133,.15); color: #25c685; }
.log-row.warn .badge { background: rgba(245,166,35,.15); color: #f5a623; }
.log-row.err .badge { background: rgba(255,93,93,.15); color: #ff5d5d; }
.log-row .sym { color: #d6dae3; flex-shrink: 0; }
.log-row .msg { color: #b6bccb; word-break: break-all; }
.empty { padding: 24px; text-align: center; color: #6b7280; }
</style>
