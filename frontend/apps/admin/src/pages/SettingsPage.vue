<script setup lang="ts">
import { computed, defineAsyncComponent, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { apiV5 } from '../api'
import type { AdminSiteV5 } from '../api-v5'
import { toastError } from '../ui'

const TABS = ['general', 'legal', 'features', 'security', 'defaults', 'payment', 'email', 'resellers', 'import'] as const
type Tab = (typeof TABS)[number]
const comps = {
  general: defineAsyncComponent(() => import('../components/settings/GeneralTab.vue')),
  legal: defineAsyncComponent(() => import('../components/settings/LegalTab.vue')),
  features: defineAsyncComponent(() => import('../components/settings/FeaturesTab.vue')),
  security: defineAsyncComponent(() => import('../components/settings/SecurityTab.vue')),
  defaults: defineAsyncComponent(() => import('../components/settings/DefaultsTab.vue')),
  payment: defineAsyncComponent(() => import('../components/settings/PaymentTab.vue')),
  email: defineAsyncComponent(() => import('../components/settings/EmailTab.vue')),
  resellers: defineAsyncComponent(() => import('../components/settings/ResellersTab.vue')),
  import: defineAsyncComponent(() => import('../components/settings/ImportTab.vue')),
} satisfies Record<Tab, unknown>

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const site = ref<AdminSiteV5 | null>(null)
const loading = ref(false)
const tab = computed<Tab>({
  get: () => (TABS as readonly string[]).includes(route.params.tab as string) ? (route.params.tab as Tab) : 'general',
  set: (v) => void router.push({ name: 'settings', params: { tab: v } }),
})
onMounted(async () => {
  loading.value = true
  try {
    site.value = await apiV5.site.get()
  } catch (e) {
    toastError(e)
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div v-loading="loading" class="page">
    <el-tabs v-model="tab" class="tabs">
      <el-tab-pane v-for="k in TABS" :key="k" :label="t(`v5.settings.tab.${k}`)" :name="k" lazy>
        <component :is="comps[k]" v-if="site && tab === k" :site="site" @saved="site = $event" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped>
.tabs {
  background: var(--a-surface);
  padding: 4px 16px 16px;
  border-radius: 8px;
}
</style>
