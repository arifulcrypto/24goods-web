<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { client } from '../api'
import { completeChallenge, siteAccessBlocked, type Challenge } from '../lib/securityChallenge'
import { toastError } from '../ui'
const {t}=useI18n()
const route=useRoute()
const protectedRoute=computed(()=>['home','product','buy','search','help','article'].includes(String(route.name)))
const blocked=ref(true),busy=ref(false),failed=ref(false)
async function check(){try{const s=await client.get<{required:boolean;cleared:boolean}>('/security/site-clearance');blocked.value=s.required&&!s.cleared;failed.value=false}catch(e){failed.value=true;toastError(e)}}
async function clear(){if(busy.value)return;busy.value=true;try{const ch=await client.post<Challenge>('/security/challenges',{action:'site_visit'});await client.post('/security/site-clearance',await completeChallenge(ch));await check()}catch(e){toastError(e)}finally{busy.value=false}}
function visible(){if(!document.hidden && protectedRoute.value)void check()}
onMounted(()=>{void check();document.addEventListener('visibilitychange',visible)})
watch(siteAccessBlocked,blockedByServer=>{if(blockedByServer){blocked.value=true;siteAccessBlocked.value=false;void check()}})
const timer=setInterval(visible,60000)
onBeforeUnmount(()=>{clearInterval(timer);document.removeEventListener('visibilitychange',visible)})
watch(()=>route.fullPath,()=>{if(protectedRoute.value)void check()})
</script>
<template>
  <section v-if="blocked && protectedRoute" class="visit-gate"><h1>{{t('challenge.visit')}}</h1><p>{{t('challenge.visitNote')}}</p><van-button v-if="!failed" type="primary" :loading="busy" @click="clear">{{t('challenge.verify')}}</van-button><van-button v-else @click="check">{{t('common.retry')}}</van-button></section>
  <slot v-else />
</template>
<style scoped>.visit-gate{max-width:440px;margin:15vh auto;padding:24px;text-align:center}</style>
