<script setup lang="ts">
/** Announcement: modal once per version (localStorage) + collapsible notice bar on Home. */
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { SafeHtml } from '@24goods/shared/ui'
import { dismissAnnouncement, isAnnouncementDismissed } from '@24goods/shared'
import { useSiteStore } from '../stores/site'

const { t } = useI18n()
const site = useSiteStore()
const modal = ref(false)
const open = ref(false)
const a = computed(() => (site.site?.announcement?.enabled === false ? null : (site.site?.announcement ?? null)))

onMounted(async () => {
  await site.load()
  if (a.value && !isAnnouncementDismissed(a.value.version)) modal.value = true
})
function dismiss() {
  if (a.value) dismissAnnouncement(a.value.version)
  modal.value = false
}
</script>

<template>
  <template v-if="a">
    <div class="notice">
      <button type="button" class="notice-head" :aria-expanded="open" @click="open = !open">
        <van-icon name="volume-o" aria-hidden="true" />
        <span class="notice-title">{{ a.title }}</span>
        <van-icon :name="open ? 'arrow-up' : 'arrow-down'" aria-hidden="true" />
        <span class="sr-only">{{ t('home.notice') }}</span>
      </button>
      <SafeHtml v-if="open" class="notice-body rich" :html="a.body_html" />
    </div>
    <van-popup v-model:show="modal" round teleport="body" class="ann" :close-on-click-overlay="false" @click-overlay="dismiss">
      <div class="ann-box" role="dialog" aria-modal="true" :aria-label="a.title">
        <h2>{{ a.title }}</h2>
        <SafeHtml class="rich" :html="a.body_html" />
        <van-button block round type="primary" size="large" @click="dismiss">{{ t('home.gotIt') }}</van-button>
      </div>
    </van-popup>
  </template>
</template>

<style scoped>
.notice {
  margin: 8px 12px 0;
  background: var(--c-accent-soft);
  border-radius: var(--radius-m);
  color: var(--c-accent-ink);
  font-size: 13px;
}
.notice-head {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  min-height: 44px;
  padding: 0 12px;
  border: 0;
  background: none;
  color: inherit;
  font: inherit;
  text-align: start;
  cursor: pointer;
}
.notice-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.notice-body {
  padding: 0 12px 12px;
}
.ann {
  width: min(86vw, 420px);
}
.ann-box {
  padding: 20px 16px 16px;
  max-height: 80vh;
  overflow: auto;
}
.ann-box h2 {
  font-size: 18px;
  margin: 0 0 10px;
}
.ann-box .rich {
  margin-bottom: 16px;
}
.rich {
  line-height: 1.6;
  overflow-wrap: anywhere;
}
.rich :deep(a) {
  color: inherit;
  text-decoration: underline;
}
.rich :deep(p),
.rich :deep(ul),
.rich :deep(ol) {
  margin: 0 0 8px;
}
</style>
