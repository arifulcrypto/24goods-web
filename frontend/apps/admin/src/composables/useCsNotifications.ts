import { onBeforeUnmount, onMounted, watch } from 'vue'
import { useRoute, useRouter, type RouteLocationRaw } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { formatMoney, messagePreview } from '@24goods/shared'
import { beep, desktopNotify } from '../notify'
import { createSeenSet, createThrottle, isRelevant, orderAlertKind, titleWithCount, type NotifyEvent } from '../lib/notifyPolicy'
import { useDeskStore } from '../stores/desk'
import { usePrefsStore } from '../stores/prefs'
import { useRealtimeStore } from '../stores/realtime'

/**
 * CS notifications (review-v3 §6, v8b §4): chime + browser Notification on a new buyer message, order
 * created / paid (any delivery mode) / cancelled / expired (per-agent toggles) and a new after-sales ticket. Only when the tab is hidden or the agent is
 * looking at something else; throttled to one alert per 4 s; tab title shows the unread count.
 * Mounted once, by AdminLayout.
 */
export function useCsNotifications() {
  const { t, locale } = useI18n()
  const route = useRoute()
  const router = useRouter()
  const rt = useRealtimeStore()
  const prefs = usePrefsStore()
  const desk = useDeskStore()
  const throttle = createThrottle()
  const orderSeen = createSeenSet()
  const ticketSeen = createSeenSet()

  const isHidden = () => document.visibilityState === 'hidden' || !document.hasFocus()
  const openConversationId = () => (route.name === 'inbox' && typeof route.query.c === 'string' ? route.query.c : null)
  const openOrderId = () => (typeof route.query.open === 'string' && route.name === 'orders' ? route.query.open : null)

  function fire(ev: NotifyEvent, title: string, body: string, to: RouteLocationRaw) {
    const hidden = isHidden()
    if (!isRelevant(ev, { hidden, openConversationId: openConversationId(), openOrderId: openOrderId() })) return
    if (hidden) desk.unread++
    if (!throttle.take()) return
    if (prefs.sound) beep()
    const go = () => void router.push(to)
    if (hidden) {
      if (prefs.notify) desktopNotify(title, body, ev.kind, go)
    } else {
      // Visible but elsewhere in the console: an in-app toast instead of an OS popup.
      const n = ElNotification({
        title,
        message: body.slice(0, 120),
        type: 'info',
        duration: 5000,
        position: 'bottom-right',
        onClick: () => {
          n.close()
          go()
        },
      })
    }
  }

  const offs = [
    rt.on('message', (e) => {
      if (e.message.sender.role !== 'buyer') return
      fire(
        { kind: 'message', conversationId: e.conversation_id },
        t('notify.newMessage', { name: e.message.sender.display_name }),
        messagePreview(e.message, t),
        { name: 'inbox', query: { c: e.conversation_id } },
      )
    }),
    rt.on('order', (o) => {
      // one alert per (order, event); per-agent toggles decide which events are shown
      const kind = orderAlertKind(o, orderSeen)
      if (!kind || !prefs.orderAlerts[kind]) return
      const amount = formatMoney(o.amount, o.currency, locale.value)
      fire(
        { kind: 'order', orderId: o.id },
        t(`notify.order.${kind}`, { no: o.order_no, title: o.title, amount }),
        kind === 'created' ? amount : o.title,
        { name: 'orders', query: { open: o.id } },
      )
    }),
    rt.on('after_sales', (tk) => {
      desk.refreshAfterSalesSoon()
      if (tk.status !== 'open' || !ticketSeen.first(tk.id)) return
      fire(
        { kind: 'after_sales', orderId: tk.order_id },
        t('notify.newTicket', { no: tk.order_no ?? tk.order?.order_no ?? '' }),
        t(`afterSales.reason.${tk.reason}`),
        { name: 'after-sales', query: { open: tk.id } },
      )
    }),
    rt.on('sync', () => desk.refreshAfterSalesSoon()),
  ]

  function onVisible() {
    if (!isHidden()) desk.unread = 0
  }
  const stopTitle = watch(
    [() => desk.unread, locale],
    () => {
      document.title = titleWithCount(t('app.title'), desk.unread)
    },
    { immediate: true },
  )

  onMounted(() => {
    document.addEventListener('visibilitychange', onVisible)
    window.addEventListener('focus', onVisible)
    void desk.refreshAfterSales()
  })
  onBeforeUnmount(() => {
    offs.forEach((o) => o())
    stopTitle()
    document.removeEventListener('visibilitychange', onVisible)
    window.removeEventListener('focus', onVisible)
    document.title = t('app.title')
  })
}
