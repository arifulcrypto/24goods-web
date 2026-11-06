<script setup lang="ts">
/** Delivery content inline in the chat, under the "Delivered" system message ("Reveal here"). */
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { DeliveryView } from '@24goods/shared/ui'
import { revealedCodes, type Order } from '@24goods/shared'
import { api } from '../api'
import { confirm, showSuccessToast, toastError } from '../ui'

const props = defineProps<{ orderId: string }>()
const { t } = useI18n()
const order = ref<Order | null>(null)
const codes = ref<string[]>([])
const revealing = ref(false)
const confirming = ref(false)

onMounted(async () => {
  try {
    order.value = await api.orders.get(props.orderId)
  } catch {
    /* the order card / link still works */
  }
})
onBeforeUnmount(() => (codes.value = []))

async function reveal() {
  if (!order.value || revealing.value) return
  if (!(await confirm(t('delivery.revealTitle'), t('delivery.revealWarn')))) return
  revealing.value = true
  try {
    codes.value = revealedCodes(await api.orders.reveal(order.value.id))
  } catch (e) {
    toastError(e)
  } finally {
    revealing.value = false
  }
}
async function confirmReceipt() {
  if (!order.value || confirming.value) return
  if (!(await confirm(t('order.confirmTitle'), t('order.confirmMessage')))) return
  confirming.value = true
  try {
    order.value = await api.orders.confirm(order.value.id)
    showSuccessToast(t('order.confirmed'))
  } catch (e) {
    toastError(e)
  } finally {
    confirming.value = false
  }
}
</script>

<template>
  <div v-if="order && (order.can_reveal || order.manual_delivery?.text || codes.length)" class="cd card">
    <DeliveryView
      compact
      :info="order.delivery_info"
      :codes="codes"
      :can-reveal="order.can_reveal"
      :revealing="revealing"
      :manual-text="order.manual_delivery?.text ?? null"
      :order-no="order.order_no"
      :after-sales-until="order.after_sales_until"
      :auto-confirm-at="order.status === 'delivered' ? order.auto_confirm_at : null"
      :can-confirm="order.status === 'delivered'"
      :confirming="confirming"
      :reveal-label="t('delivery.revealHere')"
      @reveal="reveal"
      @hide="codes = []"
      @confirm="confirmReceipt"
    />
  </div>
</template>

<style scoped>
.cd {
  margin: 4px 12px 10px;
  padding: 10px 12px;
}
</style>
