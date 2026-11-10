<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  IMAGE_MAX_BYTES,
  VOICE_MAX_MS,
  longTextDecision,
  VoiceError,
  VoiceRecorder,
  compressImage,
  imageExtension,
  isAcceptedImage,
  isVoiceSupported,
  voiceExtension,
  usePasteImage,
} from '@24goods/shared'
import { ReplyBar } from '@24goods/shared/chat'
import { showConfirmDialog, showToast } from '../ui'

const emit = defineEmits<{
  sendText: [text: string]
  sendFile: [text: string]
  sendImage: [blob: Blob, meta: { width?: number; height?: number; filename: string }]
  sendVoice: [blob: Blob, durationMs: number, filename: string]
  focus: []
  /** v11 W2: the user is typing (the page throttles the network ping). */
  typing: []
  cancelReply: []
  cancelEdit: []
  saveEdit: [text: string]
}>()
const props = withDefaults(
  defineProps<{ disabled?: boolean; voice?: boolean; reply?: { title: string; preview: string } | null; editing?: { text: string } | null }>(),
  { voice: true, reply: null, editing: null },
)
const { t } = useI18n()

const mode = ref<'text' | 'voice'>('text')
const text = ref('')
const textarea = ref<HTMLTextAreaElement | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const canSend = computed(() => text.value.trim().length > 0)

// ---------- text ----------
function autoGrow() {
  const el = textarea.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, 112)}px`
}
/** The unsent draft is stashed while an edit is open and restored when it ends (cancel or saved). */
let stash: string | null = null
watch(
  () => (props.editing ? props.editing.text : null),
  (e, prev) => {
    if (e !== null) {
      if (prev === null || prev === undefined) stash = text.value
      text.value = e
      mode.value = 'text'
      void nextTick(() => {
        autoGrow()
        textarea.value?.focus()
      })
    } else if (stash !== null) {
      text.value = stash
      stash = null
      void nextTick(autoGrow)
    }
  },
)
watch(() => props.reply, (r) => r && void nextTick(() => textarea.value?.focus()))
async function submitText() {
  const v = text.value.trim()
  if (!v) return
  if (props.editing) {
    // Keep the text: the page ends the edit on success, which restores the draft.
    emit('saveEdit', v)
    return
  }
  const decision = longTextDecision(v)
  if (decision === 'ask') {
    // default (confirm) = send as a .txt file; cancel = send as plain text
    let asFile = true
    try {
      await showConfirmDialog({
        title: t('chat.longTitle'),
        message: t('chat.longMessage', { n: [...v].length }),
        confirmButtonText: t('chat.sendAsFile'),
        cancelButtonText: t('chat.sendAsText'),
      })
    } catch {
      asFile = false
    }
    if (asFile) emit('sendFile', v)
    else emit('sendText', v)
  } else if (decision === 'file') {
    emit('sendFile', v)
  } else {
    emit('sendText', v)
  }
  text.value = ''
  void nextTick(autoGrow)
  textarea.value?.focus()
}
function onInput() {
  autoGrow()
  if (text.value.trim() && !props.editing) emit('typing')
}
function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && (props.reply || props.editing)) {
    e.preventDefault()
    if (props.editing) {
      emit('cancelEdit')
    } else emit('cancelReply')
    return
  }
  // Enter sends; Shift+Enter = newline; never during IME composition (Chinese input).
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
    e.preventDefault()
    submitText()
  }
}

// ---------- image ----------
function pickImage() {
  fileInput.value?.click()
}
usePasteImage(textarea, (files) => void sendPicked(files[0]))
async function onFile(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (file) await sendPicked(file)
}
async function sendPicked(file: File) {
  if (!isAcceptedImage(file)) {
    showToast(t('chat.image.unsupported'))
    return
  }
  try {
    const c = await compressImage(file)
    if (c.blob.size > IMAGE_MAX_BYTES) {
      showToast(t('chat.image.tooLarge'))
      return
    }
    const ext = imageExtension(c.blob.type || file.type)
    emit('sendImage', c.blob, { width: c.width || undefined, height: c.height || undefined, filename: `image.${ext}` })
  } catch {
    showToast(t('chat.image.unsupported'))
  }
}

// ---------- voice ----------
type RecState = 'idle' | 'starting' | 'holding' | 'tap'
const rec = ref<RecState>('idle')
const elapsed = ref(0)
const cancelIntent = ref(false)
let recorder: VoiceRecorder | null = null
let pressAt = 0
let startY = 0
let released = false

const remaining = computed(() => Math.max(0, Math.ceil((VOICE_MAX_MS - elapsed.value) / 1000)))
const elapsedLabel = computed(() => {
  const s = Math.floor(elapsed.value / 1000)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
})

function toggleMode() {
  if (mode.value === 'text') {
    if (!isVoiceSupported()) {
      showToast(t('chat.voice.unsupported'))
      return
    }
    mode.value = 'voice'
  } else {
    mode.value = 'text'
    void nextTick(() => textarea.value?.focus())
  }
}

function voiceErrorText(e: unknown): string {
  const code = e instanceof VoiceError ? e.code : 'failed'
  const map: Record<string, string> = {
    unsupported: 'chat.voice.unsupported',
    permission_denied: 'chat.voice.permissionDenied',
    no_device: 'chat.voice.noDevice',
    too_short: 'chat.voice.tooShort',
    failed: 'chat.voice.failed',
  }
  return t(map[code] ?? 'chat.voice.failed')
}

async function beginRecording() {
  rec.value = 'starting'
  elapsed.value = 0
  cancelIntent.value = false
  recorder = new VoiceRecorder({
    onTick: (ms) => (elapsed.value = ms),
    onMaxReached: () => {
      showToast(t('chat.voice.maxReached'))
      void finish(true)
    },
  })
  try {
    await recorder.start()
  } catch (e) {
    recorder = null
    rec.value = 'idle'
    showToast(voiceErrorText(e))
    return
  }
  // Released while the permission prompt was open → continue in tap mode.
  rec.value = released ? 'tap' : 'holding'
}

async function finish(send: boolean) {
  const r = recorder
  recorder = null
  rec.value = 'idle'
  cancelIntent.value = false
  if (!r) return
  if (!send) {
    r.cancel()
    return
  }
  try {
    const res = await r.stop()
    emit('sendVoice', res.blob, res.duration_ms, `voice.${voiceExtension(res.mime)}`)
  } catch (e) {
    if (!(e instanceof VoiceError && e.message === 'cancelled')) showToast(voiceErrorText(e))
  }
}

function onPressStart(e: PointerEvent) {
  if (rec.value === 'tap') return
  if (e.pointerType === 'mouse' && e.button !== 0) return
  e.preventDefault()
  ;(e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId)
  pressAt = Date.now()
  startY = e.clientY
  released = false
  void beginRecording()
}
function onPressMove(e: PointerEvent) {
  if (rec.value !== 'holding') return
  cancelIntent.value = startY - e.clientY > 64
}
function onPressEnd() {
  released = true
  if (rec.value === 'starting') return // will switch to tap mode once started
  if (rec.value !== 'holding') return
  if (Date.now() - pressAt < 350) {
    // quick tap → tap-to-record mode with explicit Send / Cancel
    rec.value = 'tap'
    return
  }
  void finish(!cancelIntent.value)
}
function onPressCancel() {
  released = true
  if (rec.value === 'holding') void finish(false)
}

onBeforeUnmount(() => {
  recorder?.cancel()
  recorder = null
})
</script>

<template>
  <div class="composer" :class="{ disabled }">
    <ReplyBar v-if="editing" :title="t('chatActions.editing')" :preview="editing.text" @cancel="text = ''; emit('cancelEdit')" />
    <ReplyBar v-else-if="reply" :title="reply.title" :preview="reply.preview" @cancel="emit('cancelReply')" />
    <div class="row">
      <button
        v-if="voice"
        type="button"
        class="icon-btn"
        :aria-label="mode === 'text' ? t('chat.voiceMode') : t('chat.keyboard')"
        :disabled="disabled"
        @click="toggleMode"
      >
        <van-icon :name="mode === 'text' ? 'volume-o' : 'edit'" size="24" />
      </button>

      <textarea
        v-if="mode === 'text'"
        ref="textarea"
        v-model="text"
        class="input"
        rows="1"
        maxlength="2000"
        enterkeyhint="send"
        :placeholder="t('chat.placeholder')"
        :aria-label="t('chat.placeholder')"
        :disabled="disabled"
        @input="onInput"
        @keydown="onKeydown"
        @focus="emit('focus')"
      />
      <button
        v-else
        type="button"
        class="hold"
        :class="{ active: rec === 'holding' || rec === 'starting', cancel: cancelIntent }"
        :disabled="disabled || rec === 'tap'"
        @pointerdown="onPressStart"
        @pointermove="onPressMove"
        @pointerup="onPressEnd"
        @pointercancel="onPressCancel"
        @contextmenu.prevent
      >
        {{ rec === 'holding' ? (cancelIntent ? t('chat.voice.releaseToCancel') : t('chat.voice.releaseToSend')) : t('chat.voice.holdToTalk') }}
      </button>

      <button
        v-if="mode === 'text' && canSend"
        type="button"
        class="send-btn"
        :disabled="disabled"
        @click="submitText"
      >
        {{ t('chat.send') }}
      </button>
      <button v-else type="button" class="icon-btn" :aria-label="t('chat.image.pick')" :disabled="disabled" @click="pickImage">
        <van-icon name="photo-o" size="24" />
      </button>
      <input
        ref="fileInput"
        type="file"
        accept="image/jpeg,image/png,image/webp,image/gif"
        class="sr-only"
        tabindex="-1"
        aria-hidden="true"
        @change="onFile"
      />
    </div>

    <Teleport to="body">
      <div v-if="rec !== 'idle'" class="rec-overlay" role="dialog" :aria-label="t('chat.voice.recording')">
        <div class="rec-panel" :class="{ cancel: cancelIntent }">
          <div class="rec-mic" aria-hidden="true">
            <van-icon :name="cancelIntent ? 'revoke' : 'volume'" size="40" />
            <span class="rec-bars"><i /><i /><i /><i /><i /></span>
          </div>
          <div class="rec-time">{{ elapsedLabel }} <span v-if="remaining <= 10" class="rec-left">-{{ remaining }}s</span></div>
          <div class="rec-hint">
            <template v-if="rec === 'tap'">{{ t('chat.voice.tapToStop') }}</template>
            <template v-else-if="cancelIntent">{{ t('chat.voice.releaseToCancel') }}</template>
            <template v-else>{{ t('chat.voice.slideToCancel') }}</template>
          </div>
          <div v-if="rec === 'tap'" class="rec-actions">
            <button type="button" class="rec-btn" @click="finish(false)">{{ t('chat.voice.cancel') }}</button>
            <button type="button" class="rec-btn primary" @click="finish(true)">{{ t('chat.voice.send') }}</button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.composer {
  background: #f7f7f7;
  border-top: 1px solid var(--c-line);
  padding: 6px 6px calc(6px + var(--safe-bottom));
}
.row {
  display: flex;
  align-items: flex-end;
  gap: 4px;
}
.icon-btn {
  flex: none;
  width: 44px;
  height: 44px;
  border: 0;
  background: none;
  color: var(--c-text);
  display: grid;
  place-items: center;
  border-radius: 50%;
  cursor: pointer;
}
.icon-btn:disabled {
  opacity: 0.4;
}
.input {
  flex: 1;
  min-width: 0;
  min-height: 40px;
  max-height: 112px;
  margin: 2px 0;
  padding: 9px 12px;
  border: 0;
  border-radius: 10px;
  background: var(--c-surface);
  font: inherit;
  font-size: 16px; /* ≥16px avoids iOS zoom-on-focus */
  line-height: 22px;
  resize: none;
  color: var(--c-text);
  outline: none;
}
.hold {
  flex: 1;
  min-width: 0;
  min-height: 44px;
  border: 0;
  border-radius: 10px;
  background: var(--c-surface);
  font: inherit;
  font-size: 15px;
  font-weight: 600;
  color: var(--c-text);
  touch-action: none;
  user-select: none;
  -webkit-user-select: none;
  -webkit-touch-callout: none;
  cursor: pointer;
}
.hold.active {
  background: #dcdcdc;
}
.hold.cancel {
  background: #ffe0e0;
  color: var(--c-danger);
}
.send-btn {
  flex: none;
  min-width: 60px;
  height: 40px;
  margin: 2px 2px 2px 4px;
  border: 0;
  border-radius: 10px;
  background: var(--c-accent);
  color: var(--c-on-accent);
  font-weight: 700;
  font-size: 15px;
  cursor: pointer;
}
.rec-overlay {
  position: fixed;
  inset: 0;
  z-index: 3000;
  display: grid;
  place-items: center;
  background: rgba(0, 0, 0, 0.15);
  pointer-events: none;
}
.rec-panel {
  pointer-events: auto;
  width: 180px;
  padding: 18px 16px 14px;
  border-radius: 16px;
  background: rgba(30, 30, 30, 0.86);
  color: #fff;
  text-align: center;
}
.rec-panel.cancel {
  background: rgba(200, 40, 40, 0.9);
}
.rec-mic {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  height: 56px;
}
.rec-bars {
  display: inline-flex;
  gap: 3px;
  align-items: flex-end;
  height: 28px;
}
.rec-bars i {
  width: 4px;
  background: var(--c-accent);
  border-radius: 2px;
  animation: bar 0.9s ease-in-out infinite;
}
.rec-bars i:nth-child(2) {
  animation-delay: 0.15s;
}
.rec-bars i:nth-child(3) {
  animation-delay: 0.3s;
}
.rec-bars i:nth-child(4) {
  animation-delay: 0.45s;
}
.rec-bars i:nth-child(5) {
  animation-delay: 0.6s;
}
@keyframes bar {
  0%,
  100% {
    height: 6px;
  }
  50% {
    height: 26px;
  }
}
.rec-time {
  font-variant-numeric: tabular-nums;
  font-size: 18px;
  margin-top: 4px;
}
.rec-left {
  color: var(--c-accent);
  font-size: 13px;
}
.rec-hint {
  font-size: 12px;
  opacity: 0.85;
  margin-top: 6px;
}
.rec-actions {
  display: flex;
  gap: 8px;
  margin-top: 12px;
}
.rec-btn {
  flex: 1;
  min-height: 44px;
  border-radius: 10px;
  border: 0;
  background: rgba(255, 255, 255, 0.15);
  color: #fff;
  font: inherit;
  font-weight: 600;
  cursor: pointer;
}
.rec-btn.primary {
  background: var(--c-accent);
  color: var(--c-on-accent);
}
@media (prefers-reduced-motion: reduce) {
  .rec-bars i {
    animation: none;
    height: 14px;
  }
}
</style>
