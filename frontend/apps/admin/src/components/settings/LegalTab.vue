<script setup lang="ts">
import { onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { apiV3, apiV5 } from '../../api'
import type { AdminArticle } from '../../api-v3'
import type { AdminSiteV5 } from '../../api-v5'
import { useSave } from '../../composables/useSave'
import { toastError } from '../../ui'

const props = defineProps<{ site: AdminSiteV5 }>()
const emit = defineEmits<{ saved: [s: AdminSiteV5] }>()
const { t, locale } = useI18n()
const { saving, run } = useSave()
const pages = ref<AdminArticle[]>([])
const f = reactive({ enabled: false, mode: 'checkbox' as 'checkbox' | 'modal', slugs: [] as string[] })
watch(
  () => props.site,
  (s) => {
    f.enabled = s.legal?.enabled ?? false
    f.mode = s.legal?.mode ?? 'checkbox'
    f.slugs = [...(s.legal?.page_slugs ?? [])]
  },
  { immediate: true },
)
onMounted(async () => {
  try {
    pages.value = (await apiV3.articles.list({ kind: 'page', status: 'published', page_size: 100 })).items
  } catch (e) {
    toastError(e)
  }
})
const label = (a: AdminArticle) => (locale.value === 'zh-CN' && a.title_i18n['zh-CN']) || a.title_i18n.en || a.slug
async function save() {
  const r = await run(() => apiV5.site.put({ legal: { enabled: f.enabled, mode: f.mode, page_slugs: f.slugs } }))
  if (r) emit('saved', r)
}
</script>

<template>
  <el-form label-position="top" class="wrap" @submit.prevent="save">
    <section class="panel block">
      <h2 class="h2">{{ t('v5.legal.title') }}</h2>
      <p class="muted small">{{ t('v5.legal.note') }}</p>
      <el-form-item :label="t('v5.legal.enabled')"><el-switch v-model="f.enabled" /></el-form-item>
      <el-form-item :label="t('v5.legal.mode')">
        <el-radio-group v-model="f.mode">
          <el-radio-button value="checkbox">{{ t('v5.legal.checkbox') }}</el-radio-button>
          <el-radio-button value="modal">{{ t('v5.legal.modal') }}</el-radio-button>
        </el-radio-group>
      </el-form-item>
      <el-form-item :label="t('v5.legal.pages')">
        <el-select v-model="f.slugs" multiple filterable style="width: 100%" :placeholder="t('v5.legal.pagesPlaceholder')">
          <el-option v-for="a in pages" :key="a.slug" :value="a.slug" :label="label(a)" />
        </el-select>
        <div class="muted small">{{ t('v5.legal.pagesHint') }}</div>
      </el-form-item>
    </section>
    <el-button type="primary" native-type="submit" :loading="saving">{{ t('common.save') }}</el-button>
  </el-form>
</template>

<style scoped src="./settings.css"></style>
