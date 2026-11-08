<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { TurnstileWidget } from '@24goods/shared/ui'
import { activeChallenge, cancelChallenge, challengeProgress, challengeSolving, finishChallenge } from '../lib/securityChallenge'
const {t,locale}=useI18n()
const token=ref('')
onBeforeUnmount(cancelChallenge)
</script>
<template>
  <Teleport to="body">
    <div v-if="challengeSolving" class="challenge-overlay" role="dialog" aria-modal="true" :aria-label="t('challenge.title')">
      <section><h2>{{t('challenge.title')}}</h2>
        <template v-if="activeChallenge"><TurnstileWidget :key="activeChallenge.id" v-model:token="token" :site-key="activeChallenge.turnstile.site_key" :action="activeChallenge.action" :locale="locale"/><van-button block type="primary" :disabled="!token" @click="finishChallenge(token);token=''">{{t('common.confirm')}}</van-button></template>
        <p v-else role="status">{{t('pow.running')}} {{Math.round(challengeProgress*100)}}%</p>
        <p>{{t('challenge.accessibility')}}</p><van-button block plain @click="cancelChallenge">{{t('common.cancel')}}</van-button>
      </section>
    </div>
  </Teleport>
</template>
<style scoped>.challenge-overlay{position:fixed;inset:0;background:#0008;z-index:9999;display:grid;place-items:center;padding:16px}.challenge-overlay section{width:min(360px,100%);background:var(--c-panel,#fff);color:var(--c-text,#222);border-radius:16px;padding:20px;max-height:90vh;overflow:auto}.van-button{margin-top:10px}</style>
