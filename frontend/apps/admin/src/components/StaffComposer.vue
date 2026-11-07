<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Goods, Microphone, Picture, Promotion } from '@element-plus/icons-vue'
import {
  IMAGE_MAX_BYTES,
  VoiceError,
  VoiceRecorder,
  compressImage,
  extractPastedImages,
  imageExtension,
  isAcceptedImage,
  isVoiceSupported,
  voiceExtension,
} from '@24goods/shared'

import ProductPickerDialog from './ProductPickerDialog.vue'

defineProps<{ disabled?: boolean }>()
const emit = defineEmits<{
  sendText: [text: string]
  sendImage: [blob: Blob, meta: { width?: number; height?: number; filename: string }]
  sendVoice: [blob: Blob, durationMs: number, filename: string]
  sendProduct: [productId: string]
}>()
const { t } = useI18n()
const text = ref('')
const fileInput = ref<HTMLInputElement | null>(null)
const pickerOpen = ref(false)
const recording = ref(false)
const elapsed = ref(0)
let recorder: VoiceRecorder | null = null

const elapsedLabel = computed(() => {
  const s = Math.floor(elapsed.value / 1000)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
})

function send() {
  const v = text.value.trim()
  if (!v) return
  if (v.length > 2000) {
    ElMessage.warning(t('inbox.tooLong'))
    return
  }
  emit('sendText', v)
  text.value = ''
}
function onKeydown(ev: Event | KeyboardEvent) {
  if (!(ev instanceof KeyboardEvent)) return
  const e = ev
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
    e.preventDefault()
    send()
  }
}
async function onPaste(e: ClipboardEvent) {
  const file = extractPastedImages(e)[0]
  if (file) {
    e.preventDefault()
    await sendFile(file)
  }
}
async function onFile(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (file) await sendFile(file)
}
async function sendFile(file: File) {
  if (!isAcceptedImage(file)) {
    ElMessage.warning(t('chat.image.unsupported'))
    return
  }
  try {
    const c = await compressImage(file)
    if (c.blob.size > IMAGE_MAX_BYTES) {
      ElMessage.warning(t('chat.image.tooLarge'))
      return
    }
    emit('sendImage', c.blob, {
      width: c.width || undefined,
      height: c.height || undefined,
      filename: `image.${imageExtension(c.blob.type || file.type)}`,
    })
  } catch {
    ElMessage.warning(t('chat.image.unsupported'))
  }
}

function voiceError(e: unknown) {
  const code = e instanceof VoiceError ? e.code : 'failed'
  const key =
    {
      unsupported: 'chat.voice.unsupported',
      permission_denied: 'chat.voice.permissionDenied',
      no_device: 'chat.voice.noDevice',
      too_short: 'chat.voice.tooShort',
      failed: 'chat.voice.failed',
    }[code] ?? 'chat.voice.failed'
  ElMessage.warning(t(key))
}
async function startVoice() {
  if (!isVoiceSupported()) {
    ElMessage.warning(t('chat.voice.unsupported'))
    return
  }
  elapsed.value = 0
  recorder = new VoiceRecorder({
    onTick: (ms) => (elapsed.value = ms),
    onMaxReached: () => {
      ElMessage.info(t('chat.voice.maxReached'))
      void stopVoice(true)
    },
  })
  try {
    await recorder.start()
    recording.value = true
  } catch (e) {
    recorder = null
    voiceError(e)
  }
}
async function stopVoice(sendIt: boolean) {
  const r = recorder
  recorder = null
  recording.value = false
  if (!r) return
  if (!sendIt) {
    r.cancel()
    return
  }
  try {
    const res = await r.stop()
    emit('sendVoice', res.blob, res.duration_ms, `voice.${voiceExtension(res.mime)}`)
  } catch (e) {
    if (!(e instanceof VoiceError && e.message === 'cancelled')) voiceError(e)
  }
}
onBeforeUnmount(() => recorder?.cancel())
</script>

<template>
  <div class="composer">
    <div v-if="recording" class="rec">
      <span class="dot" aria-hidden="true" />
      <span>{{ t('inbox.recording', { time: elapsedLabel }) }}</span>
      <span class="spacer" />
      <el-button @click="stopVoice(false)">{{ t('chat.voice.cancel') }}</el-button>
      <el-button type="primary" @click="stopVoice(true)">{{ t('inbox.stopAndSend') }}</el-button>
    </div>
    <template v-else>
      <div class="tools">
        <el-tooltip :content="t('chat.image.pick')">
          <el-button text circle :icon="Picture" :disabled="disabled" :aria-label="t('chat.image.pick')" @click="fileInput?.click()" />
        </el-tooltip>
        <el-tooltip :content="t('chat.voice.record')">
          <el-button text circle :icon="Microphone" :disabled="disabled" :aria-label="t('chat.voice.record')" @click="startVoice" />
        </el-tooltip>
        <el-button text :icon="Goods" :disabled="disabled" @click="pickerOpen = true">{{ t('inbox.product_btn') }}</el-button>
        <input ref="fileInput" type="file" accept="image/jpeg,image/png,image/webp,image/gif" hidden @change="onFile" />
      </div>
      <div class="input-row">
        <el-input
          v-model="text"
          type="textarea"
          :autosize="{ minRows: 2, maxRows: 6 }"
          resize="none"
          maxlength="2000"
          :placeholder="t('inbox.placeholder')"
          :disabled="disabled"
          @keydown="onKeydown"
          @paste="onPaste"
        />
        <el-button type="primary" :icon="Promotion" :disabled="disabled || !text.trim()" @click="send">{{ t('common.send') }}</el-button>
      </div>
      <div class="paste-tip">{{ t('paste.tip') }}</div>
    </template>
    <ProductPickerDialog v-model="pickerOpen" @pick="(id) => ((pickerOpen = false), emit('sendProduct', id))" />
  </div>
</template>

<style scoped>
.composer {
  border-top: 1px solid var(--a-line);
  background: var(--a-surface);
  padding: 6px 12px 10px;
}
.tools {
  display: flex;
  gap: 2px;
}
.paste-tip {
  margin-top: 4px;
  font-size: 12px;
  color: #606266;
}
.input-row {
  display: flex;
  gap: 8px;
  align-items: flex-end;
}
.rec {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 72px;
}
.dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--el-color-danger);
  animation: blink 1s infinite;
}
.spacer {
  flex: 1;
}
@keyframes blink {
  50% {
    opacity: 0.2;
  }
}
</style>
