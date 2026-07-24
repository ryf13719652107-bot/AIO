<template>
  <div class="login-wrap">
    <div class="login-card">
      <div class="brand">
        <div class="logo">⚡</div>
        <div class="title">Binance 量化策略面板</div>
        <div class="subtitle">RSI + MACD 多周期组合策略</div>
      </div>
      <el-form :model="form" @submit.prevent="onLogin" label-position="top">
        <el-form-item label="账号">
          <el-input v-model="form.username" autofocus placeholder="admin" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="form.password" type="password" show-password placeholder="••••••" @keyup.enter="onLogin" />
        </el-form-item>
        <el-button type="primary" :loading="loading" style="width:100%" @click="onLogin">登录</el-button>
      </el-form>
      <div class="tip">默认账号请见后端 .env 中的 ADMIN_USERNAME / ADMIN_PASSWORD</div>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { authApi } from '../api'
import { useAuthStore } from '../store/auth'
import { ElMessage } from 'element-plus'

const form = reactive({ username: '', password: '' })
const loading = ref(false)
const router = useRouter()
const auth = useAuthStore()

async function onLogin() {
  if (!form.username || !form.password) {
    ElMessage.warning('请输入账号和密码')
    return
  }
  loading.value = true
  try {
    const data = await authApi.login(form.username, form.password)
    auth.setToken(data.access_token, form.username)
    router.push({ name: 'dashboard' })
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-wrap {
  display: flex; align-items: center; justify-content: center;
  min-height: 100vh;
  background: radial-gradient(circle at 30% 20%, #1c2333 0%, #0a0c10 100%);
}
.login-card {
  width: 380px; padding: 32px 32px 24px;
  background: #161a23; border: 1px solid #232a39;
  border-radius: 12px;
  box-shadow: 0 20px 60px rgba(0,0,0,.4);
}
.brand { text-align: center; margin-bottom: 24px; }
.logo { font-size: 40px; }
.title { font-size: 20px; font-weight: 600; margin-top: 8px; color: #e2e6ee; }
.subtitle { font-size: 13px; color: #8a93a7; margin-top: 4px; }
.tip { margin-top: 16px; font-size: 12px; color: #6b7280; text-align: center; }
:deep(.el-form-item__label) { color: #b6bccb; }
</style>
