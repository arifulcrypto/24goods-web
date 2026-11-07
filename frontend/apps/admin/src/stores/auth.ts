import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import type { User } from '@24goods/shared'
import { ApiError, isApiError } from '@24goods/shared'
import { api } from '../api'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const ready = ref(false)
  let loading: Promise<void> | null = null

  const isStaff = computed(() => user.value?.role === 'agent' || user.value?.role === 'admin')
  const isAdmin = computed(() => user.value?.role === 'admin')

  function init(): Promise<void> {
    if (ready.value) return Promise.resolve()
    if (!loading) {
      loading = api.auth
        .me()
        .then((u) => {
          user.value = u
        })
        .catch(() => {
          user.value = null
        })
        .finally(() => {
          ready.value = true
          loading = null
        })
    }
    return loading
  }

  /** Staff-only login: a buyer session is immediately logged out again. */
  async function login(email: string, password: string, turnstile_token?: string): Promise<User> {
    const u = await api.auth.login({ email, password, ...(turnstile_token ? { turnstile_token } : {}) })
    if (u.role !== 'agent' && u.role !== 'admin') {
      await api.auth.logout().catch(() => undefined)
      user.value = null
      throw new ApiError('not_staff', 'not staff', 403)
    }
    user.value = u
    ready.value = true
    return u
  }
  async function logout() {
    try {
      await api.auth.logout()
    } catch (e) {
      if (!isApiError(e)) throw e
    } finally {
      user.value = null
    }
  }
  function clear() {
    user.value = null
  }
  return { user, ready, isStaff, isAdmin, init, login, logout, clear }
})
