import { createApp } from 'vue'
import { createPinia } from 'pinia'
import './styles/tokens.css'
import './styles/base.css'
import App from './App.vue'
import { i18n, currentLocale } from './i18n'
import { applyVantLocale } from './locale'
import { router, handleUnauthorized } from './router'
import { setUnauthorizedHandler } from './api'

const app = createApp(App)
app.use(createPinia())
app.use(i18n)
app.use(router)
applyVantLocale(currentLocale())
setUnauthorizedHandler(() => handleUnauthorized())
app.mount('#app')
