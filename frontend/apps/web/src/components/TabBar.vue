<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useConversationsStore } from '../stores/conversations'
import { useCartStore } from '../stores/cart'
import { useSiteStore } from '../stores/site'

const route = useRoute()
const { t } = useI18n()
const convs = useConversationsStore()
const cart = useCartStore()
const site = useSiteStore()
const cartBadge = computed(() => (cart.count > 99 ? '99+' : cart.count || ''))
const active = computed(() => route.meta.tab ?? 'home')
const unread = computed(() => (convs.totalUnread > 99 ? '99+' : convs.totalUnread || ''))
</script>

<template>
  <nav class="dock tabbar-dock" :aria-label="site.headerName">
    <van-tabbar :model-value="active" :fixed="false" :border="true" safe-area-inset-bottom>
      <van-tabbar-item name="home" to="/" icon="wap-home-o">{{ t('tabs.home') }}</van-tabbar-item>
      <van-tabbar-item name="messages" to="/messages" icon="chat-o" :badge="unread">{{ t('tabs.messages') }}</van-tabbar-item>
      <van-tabbar-item v-if="site.features.cart" name="cart" to="/cart" icon="cart-o" :badge="cartBadge">{{ t('tabs.cart') }}</van-tabbar-item>
      <van-tabbar-item name="me" to="/me" icon="user-o">{{ t('tabs.me') }}</van-tabbar-item>
    </van-tabbar>
  </nav>
</template>

<style scoped>
.tabbar-dock {
  background: var(--c-surface);
  box-shadow: 0 -1px 0 var(--c-line);
}
.tabbar-dock :deep(.van-tabbar-item--active) {
  font-weight: 600;
}
.tabbar-dock :deep(.van-tabbar-item--active .van-icon) {
  color: var(--c-text);
  background: var(--c-accent);
  border-radius: 999px;
  padding: 2px 10px;
}
</style>
