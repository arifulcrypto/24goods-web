<script setup lang="ts">
import { nextTick, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { apiV5 } from '../../api'
import { emailTestErrorKey, type AdminSiteV5 } from '../../api-v5'
import { PLACEHOLDERS, TEMPLATE_NAMES, insertPlaceholder, type TemplateName } from '../../lib/emailTemplates'
import { useSave } from '../../composables/useSave'
import { confirmBox, toastError, toastOk } from '../../ui'

const props = defineProps<{ site: AdminSiteV5 }>()
const emit = defineEmits<{ saved: [s: AdminSiteV5] }>()
const { t } = useI18n()
const { saving, run } = useSave()
type Form = { subject: { en: string; 'zh-CN': string }; body: { en: string; 'zh-CN': string } }
const forms = reactive({} as Record<TemplateName, Form>)
const name = ref<TemplateName>('verify_code')
const lang = ref<'en' | 'zh-CN'>('en')
const testTo = ref('')
const testing = ref(false)
const focus = ref<'subject' | 'body'>('body')
const subjectRef = ref<{ input?: HTMLInputElement } | null>(null)
const bodyRef = ref<{ textarea?: HTMLTextAreaElement } | null>(null)

watch(
  () => props.site.email_templates,
  (tpl) => {
    for (const n of TEMPLATE_NAMES) {
      const x = tpl?.[n]
      forms[n] = {
        subject: { en: x?.subject_i18n?.en ?? '', 'zh-CN': x?.subject_i18n?.['zh-CN'] ?? '' },
        body: { en: x?.body_i18n?.en ?? '', 'zh-CN': x?.body_i18n?.['zh-CN'] ?? '' },
      }
    }
  },
  { immediate: true },
)
const body = (n: TemplateName) => ({ subject_i18n: { ...forms[n].subject }, body_i18n: { ...forms[n].body } })

function insert(ph: string) {
  const el = focus.value === 'subject' ? subjectRef.value?.input : bodyRef.value?.textarea
  const field = forms[name.value][focus.value]
  const cur = field[lang.value]
  const r = insertPlaceholder(cur, el?.selectionStart ?? cur.length, el?.selectionEnd ?? cur.length, ph)
  field[lang.value] = r.text
  void nextTick(() => {
    el?.focus()
    el?.setSelectionRange(r.caret, r.caret)
  })
}
async function save() {
  const f = forms[name.value]
  if (!f.subject.en.trim() || !f.body.en.trim()) {
    toastError(new Error('validation'))
    return
  }
  const r = await run(() => apiV5.site.put({ email_templates: { [name.value]: body(name.value) } }))
  if (r) emit('saved', r)
}
async function reset() {
  if (!(await confirmBox(t('v5.tpl.resetConfirm', { name: t(`v5.tpl.n.${name.value}`) })))) return
  const r = await run(() => apiV5.site.put({ email_templates: { [name.value]: null } }))
  if (r) emit('saved', r)
}
async function sendTest() {
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(testTo.value.trim())) {
    toastError(new Error('validation'))
    return
  }
  testing.value = true
  try {
    // Tests the saved template: save first if edited.
    const r = await apiV5.site.testEmail(testTo.value.trim(), name.value)
    if (r.ok) toastOk(t('v5.email.testOk'))
    else ElMessage.error({ message: `${t('v5.email.testFailed')}${r.error ? `: ${emailTestErrorKey(r.error) ? t(emailTestErrorKey(r.error)!) : r.error}` : ''}`, grouping: true })
  } catch (e) {
    toastError(e)
  } finally {
    testing.value = false
  }
}
</script>

<template>
  <section v-if="forms[name]" class="panel block tpl">
    <h2 class="h2">{{ t('v5.tpl.title') }}</h2>
    <p class="muted small">{{ t('v5.tpl.note') }}</p>
    <el-radio-group v-model="name" class="gap">
      <el-radio-button v-for="n in TEMPLATE_NAMES" :key="n" :value="n">{{ t(`v5.tpl.n.${n}`) }}</el-radio-button>
    </el-radio-group>
    <el-tabs v-model="lang">
      <el-tab-pane :label="t('products.form.tabEn')" name="en" />
      <el-tab-pane :label="t('products.form.tabZh')" name="zh-CN" />
    </el-tabs>
    <div class="chips" role="group" :aria-label="t('v5.tpl.placeholders')">
      <span class="muted small">{{ t('v5.tpl.placeholders') }}</span>
      <el-button v-for="p in PLACEHOLDERS" :key="p" size="small" round class="mono" @click="insert(p)">{{ p }}</el-button>
    </div>
    <el-form label-position="top" @submit.prevent>
      <el-form-item :label="t('v5.tpl.subject')">
        <el-input ref="subjectRef" v-model="forms[name].subject[lang]" maxlength="200" show-word-limit @focus="focus = 'subject'" />
      </el-form-item>
      <el-form-item :label="t('v5.tpl.body')">
        <el-input ref="bodyRef" v-model="forms[name].body[lang]" type="textarea" :rows="9" maxlength="5000" show-word-limit @focus="focus = 'body'" />
        <div class="muted small">{{ t('v5.tpl.bodyHint') }}</div>
      </el-form-item>
    </el-form>
    <el-button type="primary" :loading="saving" @click="save">{{ t('v5.tpl.save') }}</el-button>
    <el-button @click="reset">{{ t('v5.tpl.reset') }}</el-button>
    <div class="test">
      <el-input v-model="testTo" inputmode="email" :placeholder="t('v5.email.testTo')" style="max-width: 280px" />
      <el-button :loading="testing" @click="sendTest">{{ t('v5.tpl.sendTest') }}</el-button>
      <span class="muted small">{{ t('v5.tpl.testNote') }}</span>
    </div>
  </section>
</template>

<style scoped src="./settings.css"></style>
<style scoped>
.tpl {
  margin-top: 16px;
}
.gap {
  margin-bottom: 8px;
  flex-wrap: wrap;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-bottom: 12px;
}
.chips .el-button + .el-button {
  margin-left: 0;
}
.test {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 16px;
}
</style>
