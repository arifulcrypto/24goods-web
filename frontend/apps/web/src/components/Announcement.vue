<script setup lang="ts">
/**
 * Announcement (v9 §8): pop-up once per version (localStorage), optional dismissible home banner,
 * and a mandatory acknowledgement (no close button until "I have read and understood"; signed-in
 * buyers also store it server-side so it follows them across devices).
 */
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { SafeHtml } from '@24goods/shared/ui'
import {
  announcementPlan,
  dismissAnnouncement,
  dismissAnnouncementBanner,
  isAnnouncementBannerDismissed,
  isAnnouncementDismissed,
  localAnnouncementAck,
  rememberAnnouncementAck,
} from '@24goods/shared'
import { api } from '../api'
import { useAuthStore } from '../stores/auth'
import { useSiteStore } from '../stores/site'

const { t } = useI18n()
const site = useSiteStore()
const auth = useAuthStore()
const modal = ref(false)
const open = ref(false)
const bannerGone = ref(false)
const serverAck = ref(0)
const a = computed(() => (site.site?.announcement?.enabled === false ? null : (site.site?.announcement ?? null)))
const plan = computed(() => {
  const cur = a.value
  if (!cur) return null
  return announcementPlan(cur, {
    dismissed: isAnnouncementDismissed(cur.version),
    bannerDismissed: bannerGone.value || isAnnouncementBannerDismissed(cur.version),
    acked: Math.max(localAnnouncementAck(), serverAck.value),
  })
})

onMounted(async () => {
  await site.load()
  await auth.init()
  if (auth.loggedIn) {
    try {
      serverAck.value = (await api.me.announcementAck()).version
    } catch {
      /* the local record still applies */
    }
  }
  modal.value = !!plan.value?.modal
})
function dismiss() {
  if (a.value) dismissAnnouncement(a.value.version)
  modal.value = false
}
async function acknowledge() {
  const cur = a.value
  if (!cur) return
  rememberAnnouncementAck(cur.version)
  dismissAnnouncement(cur.version)
  serverAck.value = Math.max(serverAck.value, Number(cur.version))
  modal.value = false
  if (auth.loggedIn) {
    try {
      await api.me.acknowledgeAnnouncement(Number(cur.version))
    } catch {
      /* stored locally; it will be retried on the next acknowledgement */
    }
  }
}
function closeBanner() {
  if (a.value) dismissAnnouncementBanner(a.value.version)
  bannerGone.value = true
}
</script>

<template>
  <template v-if="a && plan">
    <div v-if="plan.noticeBar || plan.banner" class="notice" :class="{ strip: plan.banner }">
      <button type="button" class="notice-head" :aria-expanded="open" @click="open = !open">
        <van-icon name="volume-o" aria-hidden="true" />
        <span class="notice-title">{{ a.title }}</span>
        <van-icon :name="open ? 'arrow-up' : 'arrow-down'" aria-hidden="true" />
        <span class="sr-only">{{ t('home.notice') }}</span>
      </button>
      <button v-if="plan.banner" type="button" class="notice-close" :aria-label="t('home.dismissNotice')" @click="closeBanner">
        <van-icon name="cross" aria-hidden="true" />
      </button>
      <SafeHtml v-if="open" class="notice-body rich" :html="a.body_html" />
    </div>
    <van-popup
      v-model:show="modal"
      round
      teleport="body"
      class="ann"
      :close-on-click-overlay="false"
      :close-on-popstate="false"
      @click-overlay="plan.closable && dismiss()"
    >
      <div class="ann-box" role="dialog" aria-modal="true" :aria-label="a.title">
        <h2>{{ a.title }}</h2>
        <SafeHtml class="rich" :html="a.body_html" />
        <van-button v-if="plan.closable" block round type="primary" size="large" @click="dismiss">{{ t('home.gotIt') }}</van-button>
        <van-button v-else block round type="primary" size="large" data-testid="ann-ack" @click="acknowledge">{{ t('home.ackRead') }}</van-button>
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
.notice {
  position: relative;
}
.notice-close {
  position: absolute;
  top: 0;
  inset-inline-end: 0;
  width: 44px;
  height: 44px;
  border: 0;
  background: none;
  color: inherit;
  cursor: pointer;
}
.notice.strip .notice-head {
  padding-inline-end: 44px;
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
