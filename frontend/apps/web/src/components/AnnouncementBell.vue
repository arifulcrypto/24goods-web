<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useAnnouncementsStore } from '../stores/announcements'

const { t } = useI18n()
const store = useAnnouncementsStore()
const count = computed(() => (store.showBadge ? store.unread : 0))
</script>

<template>
  <RouterLink to="/announcements" class="bell" :aria-label="count ? t('news.bellUnread', { n: count }) : t('news.title')">
    <van-icon name="bell" size="22" aria-hidden="true" />
    <span v-if="count" class="bell-badge" aria-hidden="true">{{ count > 99 ? '99+' : count }}</span>
  </RouterLink>
</template>

<style scoped>
.bell {
  position: relative;
  display: inline-grid;
  place-items: center;
  width: 44px;
  height: 44px;
  color: var(--c-text);
}
.bell-badge {
  position: absolute;
  top: 4px;
  inset-inline-end: 2px;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  border-radius: 8px;
  background: var(--c-danger, #ee0a24);
  color: #fff;
  font-size: 10px;
  line-height: 16px;
  text-align: center;
}
</style>
