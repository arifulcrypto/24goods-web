<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { apiV5 } from '../../api'
import type { AdminSiteV5 } from '../../api-v5'
import { useSave } from '../../composables/useSave'

const props = defineProps<{ site: AdminSiteV5 }>()
const emit = defineEmits<{ saved: [s: AdminSiteV5] }>()
const { t } = useI18n()
const { saving, run } = useSave()
const errors = ref<Record<string, string>>({})
const f = reactive({ indexing: true, storefronts: false, desc_en: '', desc_zh: '', og: '' })
watch(
  () => props.site.seo,
  (s) => {
    f.indexing = s?.indexing_enabled ?? true
    f.storefronts = s?.index_storefronts ?? false
    f.desc_en = s?.default_description_i18n?.en ?? ''
    f.desc_zh = s?.default_description_i18n?.['zh-CN'] ?? ''
    f.og = s?.og_image ?? ''
  },
  { immediate: true },
)
async function save() {
  const e: Record<string, string> = {}
  const og = f.og.trim()
  if (og && !/^(https:\/\/|\/(?!\/))/.test(og)) e.og = t('v5.seo.ogInvalid')
  errors.value = e
  if (Object.keys(e).length) return
  const r = await run(() =>
    apiV5.site.put({
      seo: {
        indexing_enabled: f.indexing,
        index_storefronts: f.storefronts,
        default_description_i18n: { en: f.desc_en.trim(), 'zh-CN': f.desc_zh.trim() },
        og_image: og,
      },
    }),
  )
  if (r) emit('saved', r)
}
</script>

<template>
  <el-form label-position="top" class="wrap" @submit.prevent="save">
    <section class="panel block">
      <h2 class="h2">{{ t('v5.seo.title') }}</h2>
      <p class="muted small">{{ t('v5.seo.note') }}</p>
      <el-form-item :label="t('v5.seo.indexing')">
        <el-switch v-model="f.indexing" />
        <div class="muted small full">{{ t('v5.seo.indexingHint') }}</div>
      </el-form-item>
      <el-form-item :label="t('v5.seo.storefronts')">
        <el-switch v-model="f.storefronts" :disabled="!f.indexing" />
        <div class="muted small full">{{ t('v5.seo.storefrontsHint') }}</div>
      </el-form-item>
      <el-form-item :label="`${t('v5.seo.description')} (EN)`">
        <el-input v-model="f.desc_en" type="textarea" :rows="2" maxlength="300" show-word-limit />
      </el-form-item>
      <el-form-item :label="`${t('v5.seo.description')} (中文)`">
        <el-input v-model="f.desc_zh" type="textarea" :rows="2" maxlength="300" show-word-limit />
      </el-form-item>
      <el-form-item :label="t('v5.seo.og')" :error="errors.og">
        <el-input v-model="f.og" maxlength="500" placeholder="/og.png" />
        <div class="muted small full">{{ t('v5.seo.ogHint') }}</div>
      </el-form-item>
    </section>
    <el-button type="primary" native-type="submit" :loading="saving">{{ t('common.save') }}</el-button>
  </el-form>
</template>

<style scoped src="./settings.css"></style>
<style scoped>
.full {
  width: 100%;
}
</style>
