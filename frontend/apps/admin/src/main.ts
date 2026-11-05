import { createApp } from 'vue'
import { createPinia } from 'pinia'
import 'element-plus/theme-chalk/base.css'
import './styles/admin.css'
import App from './App.vue'
import { i18n } from './i18n'
import { router, handleUnauthorized } from './router'
import { setUnauthorizedHandler } from './api'

const app = createApp(App)
app.use(createPinia())
app.use(i18n)
app.use(router)
setUnauthorizedHandler(() => handleUnauthorized())
app.mount('#app')
