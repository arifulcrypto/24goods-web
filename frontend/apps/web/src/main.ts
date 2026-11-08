import { createApp } from 'vue'
import { createPinia } from 'pinia'
import './styles/tokens.css'
import './styles/base.css'
import App from './App.vue'
import { i18n, currentLocale } from './i18n'
import { applyVantLocale } from './locale'
import { router, handleUnauthorized } from './router'
import { setUnauthorizedHandler } from './api'
import { SMART_TEXT_KEY } from '@24goods/shared/chat'
import { captureStore, printCopyrightNotice } from '@24goods/shared'
import { confirmOpenLink, showToast } from './ui'

printCopyrightNotice({ owner: 'Kesiv' })
// v5 §7: capture ?store=slug before the first API call
captureStore(window.location.search)
const app = createApp(App)
app.use(createPinia())
app.use(i18n)
app.use(router)
// Chat chips: Vant dialog + toast for the UI-neutral SmartText component.
app.provide(SMART_TEXT_KEY, { confirmOpenUrl: confirmOpenLink, toast: (m: string) => showToast(m) })
applyVantLocale(currentLocale())
setUnauthorizedHandler(() => handleUnauthorized())
app.mount('#app')
