import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import type { InstantCredentials, Locale, PowSolution, User } from '@24goods/shared'
import { isApiError } from '@24goods/shared'
import { api } from '../api'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const ready = ref(false)
  /** Credentials of an instant account created in this tab (memory only, shown once). */
  const guestCreds = ref<InstantCredentials | null>(null)
  let loading: Promise<void> | null = null

  const loggedIn = computed(() => !!user.value)
  const isStaff = computed(() => user.value?.role === 'agent' || user.value?.role === 'admin')

  /** Probe /auth/me once (401 → guest). */
  function init(): Promise<void> {
    if (ready.value) return Promise.resolve()
    if (!loading) {
      loading = api.auth
        .me()
        .then((u) => {
          user.value = u
        })
        .catch((e: unknown) => {
          if (!isApiError(e) || e.status !== 401) console.warn('auth probe failed', isApiError(e) ? e.code : e)
          user.value = null
        })
        .finally(() => {
          ready.value = true
          loading = null
        })
    }
    return loading
  }

  async function login(email: string, password: string, pow?: PowSolution) {
    user.value = await api.auth.login(pow ? { email, password, pow } : { email, password })
    ready.value = true
    return user.value
  }
  async function register(b: { email: string; password: string; display_name: string; locale: Locale; pow?: PowSolution }) {
    user.value = await api.auth.register(b)
    ready.value = true
    return user.value
  }
  async function instant(pow: PowSolution): Promise<InstantCredentials> {
    const r = await api.auth.instant({ pow })
    user.value = r.user
    ready.value = true
    guestCreds.value = r.credentials
    return r.credentials
  }
  function setUser(u: User) {
    user.value = u
    ready.value = true
  }
  async function logout() {
    try {
      await api.auth.logout()
    } finally {
      user.value = null
      guestCreds.value = null
    }
  }
  /** Called by the global 401 handler. */
  function clear() {
    user.value = null
  }

  return { user, ready, guestCreds, instant, loggedIn, isStaff, init, login, register, logout, clear, setUser }
})
