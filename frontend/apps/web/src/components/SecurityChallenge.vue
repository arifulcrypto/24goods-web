<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { TurnstileWidget } from '@24goods/shared/ui'
import { activeChallenge, cancelChallenge, challengeProgress, challengeSolving, finishChallenge } from '../lib/securityChallenge'
const {t,locale}=useI18n()
const token=ref('')
const dialog=ref<HTMLElement|null>(null)
let previousFocus:HTMLElement|null=null
let previousInert=false
function restoreShell(){const shell=document.querySelector<HTMLElement>('.shell');if(shell)shell.inert=previousInert}
function focusable(){return Array.from(dialog.value?.querySelectorAll<HTMLElement>('button:not([disabled]),a[href],input:not([disabled]),iframe,[tabindex="0"]')??[])}
function keys(event:KeyboardEvent){
  if(!challengeSolving.value)return
  if(event.key==='Escape'){event.preventDefault();cancelChallenge();return}
  if(event.key==='Tab'){const nodes=focusable();const first=nodes[0],last=nodes.at(-1);if(!first){event.preventDefault();dialog.value?.focus();return}if(event.shiftKey&&(document.activeElement===first||document.activeElement===dialog.value)){event.preventDefault();last?.focus()}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus()}}
}
watch(challengeSolving,async running=>{if(running){previousFocus=document.activeElement as HTMLElement|null;const shell=document.querySelector<HTMLElement>('.shell');previousInert=shell?.inert??false;if(shell)shell.inert=true;document.addEventListener('keydown',keys,true);await nextTick();dialog.value?.focus()}else{restoreShell();document.removeEventListener('keydown',keys,true);if(previousFocus?.isConnected)previousFocus.focus();token.value=''}})
onBeforeUnmount(()=>{restoreShell();document.removeEventListener('keydown',keys,true)})
onBeforeUnmount(cancelChallenge)
</script>
<template>
  <Teleport to="body">
    <div v-if="challengeSolving" class="challenge-overlay" role="dialog" aria-modal="true" :aria-label="t('challenge.title')">
      <section ref="dialog" tabindex="-1"><h2>{{t('challenge.title')}}</h2>
        <template v-if="activeChallenge"><TurnstileWidget :key="activeChallenge.id" v-model:token="token" :site-key="activeChallenge.turnstile.site_key" :action="activeChallenge.action" :locale="locale"/><van-button block type="primary" :disabled="!token" @click="finishChallenge(token);token=''">{{t('common.confirm')}}</van-button></template>
        <p v-else role="status">{{t('pow.running')}} {{Math.round(challengeProgress*100)}}%</p>
        <p>{{t('challenge.accessibility')}}</p><van-button block plain @click="cancelChallenge">{{t('common.cancel')}}</van-button>
      </section>
    </div>
  </Teleport>
</template>
<style scoped>.challenge-overlay{position:fixed;inset:0;background:#0008;z-index:9999;display:grid;place-items:center;padding:16px}.challenge-overlay section{width:min(360px,100%);background:var(--c-panel,#fff);color:var(--c-text,#222);border-radius:16px;padding:20px;max-height:90vh;overflow:auto}.van-button{margin-top:10px}</style>
