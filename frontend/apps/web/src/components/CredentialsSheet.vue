<script setup lang="ts">
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { BRAND, copyText, downloadText, type InstantCredentials } from '@24goods/shared'
import { showToast } from '../ui'

const props = defineProps<{ show: boolean; credentials: InstantCredentials | null }>()
const emit = defineEmits<{ done: [] }>()
const { t } = useI18n()
const saved = ref(false)
watch(
  () => props.show,
  (v) => v && (saved.value = false),
)

async function copy(s: string) {
  showToast((await copyText(s)) ? t('common.copied') : t('common.copyFailed'))
}
function save() {
  const c = props.credentials
  if (!c) return
  downloadText(
    `${BRAND.name.toLowerCase()}-credentials.txt`,
    `${BRAND.name}\n${t('cred.login')}: ${c.login}\n${t('cred.password')}: ${c.password}\n`,
  )
}
</script>

<template>
  <van-popup
    :show="show"
    position="bottom"
    round
    safe-area-inset-bottom
    teleport="body"
    class="buy-sheet"
    :close-on-click-overlay="false"
  >
    <div v-if="credentials" class="cred" role="dialog" aria-modal="true" :aria-label="t('cred.title')">
      <h2>{{ t('cred.title') }}</h2>
      <p class="muted">{{ t('cred.warn') }}</p>
      <div class="row">
        <div class="val">
          <small>{{ t('cred.login') }}</small>
          <code>{{ credentials.login }}</code>
        </div>
        <van-button size="small" round class="tap" @click="copy(credentials.login)">{{ t('common.copy') }}</van-button>
      </div>
      <div class="row">
        <div class="val">
          <small>{{ t('cred.password') }}</small>
          <code>{{ credentials.password }}</code>
        </div>
        <van-button size="small" round class="tap" @click="copy(credentials.password)">{{ t('common.copy') }}</van-button>
      </div>
      <van-button block round plain class="gap" @click="save">{{ t('cred.save') }}</van-button>
      <van-checkbox v-model="saved" class="chk">{{ t('cred.saved') }}</van-checkbox>
      <van-button block round type="primary" size="large" :disabled="!saved" @click="emit('done')">
        {{ t('cred.continue') }}
      </van-button>
    </div>
  </van-popup>
</template>

<style scoped>
.cred {
  padding: 20px 16px 16px;
}
h2 {
  font-size: 18px;
  margin: 0 0 6px;
}
.muted {
  font-size: 13px;
  margin: 0 0 12px;
}
.row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 8px 8px 12px;
  border-radius: 10px;
  background: #faf8f2;
  border: 1px dashed #e7d9a8;
  margin-bottom: 8px;
}
.val {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.val small {
  color: var(--c-muted);
  font-size: 11px;
}
code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 16px;
  overflow-wrap: anywhere;
  user-select: all;
}
.gap {
  margin: 8px 0 12px;
}
.chk {
  margin-bottom: 14px;
  min-height: 44px;
}
</style>
