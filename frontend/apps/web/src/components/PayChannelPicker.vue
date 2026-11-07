<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { isUsdtChannel } from '@24goods/shared'
import { useSiteStore } from '../stores/site'

/**
 * Payment method / USDT network picker. Shown only when more than one channel is enabled
 * (GET /site/payment-channels). The pick only chooses which channel the server is asked to
 * open: price, fee and amount are always the server's.
 */
const { t } = useI18n()
const site = useSiteStore()
void site.loadChannels()
</script>

<template>
  <div v-if="site.channels && site.channels.length > 1" class="pcp" role="radiogroup" :aria-label="t('pay.method')">
    <div class="muted small">{{ t('pay.method') }}</div>
    <button
      v-for="c in site.channels"
      :key="c.id"
      type="button"
      class="opt"
      :class="{ on: site.channel?.id === c.id }"
      role="radio"
      :aria-checked="site.channel?.id === c.id"
      @click="site.select(c.id)"
    >
      <span class="lbl">{{ c.label }}</span>
      <span v-if="isUsdtChannel(c.id)" class="muted small">{{ t('pay.networkHint') }}</span>
      <span v-if="c.permille > 0" class="muted small">{{ t('pay.rate', { rate: c.fee_rate }) }}</span>
    </button>
  </div>
</template>

<style scoped>
.pcp {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 8px 0;
}
.opt {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 10px;
  min-height: 44px;
  padding: 8px 12px;
  text-align: left;
  background: var(--van-background-2, #fff);
  border: 1px solid var(--van-border-color, #ebedf0);
  border-radius: 8px;
  color: inherit;
  cursor: pointer;
}
.opt.on {
  border-color: var(--van-primary-color, #1989fa);
  box-shadow: 0 0 0 1px var(--van-primary-color, #1989fa) inset;
}
.lbl {
  font-weight: 600;
}
</style>
