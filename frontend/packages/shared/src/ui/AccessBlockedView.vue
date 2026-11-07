<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { formatDateTime } from '../format'
import type { AccessBlock } from '../api/accessBlock'

const props = defineProps<{ block: AccessBlock }>()
const { t, locale } = useI18n()
const suspended = computed(() => props.block.kind === 'account_suspended')
</script>

<template>
  <main class="blk" role="alert">
    <div class="box">
      <h1>{{ suspended ? t('blocked.suspendedTitle') : t('blocked.ipTitle') }}</h1>
      <p>{{ suspended ? t('blocked.suspendedBody') : t('blocked.ipBody') }}</p>
      <dl v-if="suspended">
        <template v-if="block.reason">
          <dt>{{ t('blocked.reason') }}</dt>
          <dd>{{ block.reason }}</dd>
        </template>
        <dt>{{ t('blocked.until') }}</dt>
        <dd>{{ block.until ? formatDateTime(block.until, locale) : t('blocked.untilForever') }}</dd>
      </dl>
      <p class="hint">{{ t('blocked.contact') }}</p>
    </div>
  </main>
</template>

<style scoped>
.blk {
  position: fixed;
  inset: 0;
  z-index: 9999;
  display: grid;
  place-items: center;
  padding: 24px 16px;
  background: #fff;
  color: #1f2328;
  overflow: auto;
}
.box {
  max-width: 420px;
  width: 100%;
  text-align: center;
}
h1 {
  font-size: 22px;
  margin: 0 0 12px;
}
p {
  margin: 0 0 12px;
  line-height: 1.5;
}
dl {
  text-align: left;
  margin: 16px 0;
  padding: 12px 16px;
  background: #f6f8fa;
  border-radius: 8px;
}
dt {
  font-size: 12px;
  color: #656d76;
}
dd {
  margin: 0 0 8px;
  word-break: break-word;
}
.hint {
  font-size: 13px;
  color: #656d76;
}
</style>
