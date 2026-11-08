import { createApp } from 'vue'
import { createPinia } from 'pinia'
import 'element-plus/theme-chalk/base.css'
import './styles/admin.css'
import App from './App.vue'
import { i18n } from './i18n'
import { router, handleUnauthorized } from './router'
import { setUnauthorizedHandler } from './api'
import { SMART_TEXT_KEY } from '@24goods/shared/chat'
import { confirmBox, toastOk } from './ui'
import { printCopyrightNotice } from '@24goods/shared'

printCopyrightNotice({ owner: 'Kesiv' })
const app = createApp(App)
app.use(createPinia())
app.use(i18n)
app.use(router)
setUnauthorizedHandler(() => handleUnauthorized())
// v4 §7: smart chat chips (links confirm before opening, copy toasts)
app.provide(SMART_TEXT_KEY, {
  confirmOpenUrl: (url: string) =>
    confirmBox(`${url}\n\n${i18n.global.t('chat.smart.openLinkWarn')}`, i18n.global.t('chat.smart.openLinkTitle')),
  toast: (m: string) => toastOk(m),
})
app.mount('#app')
