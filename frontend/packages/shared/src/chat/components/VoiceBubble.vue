<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { formatVoiceDuration } from '../../format'
import { claimAudio, releaseAudio } from './audio'

const props = defineProps<{ src: string | null; durationMs: number; self: boolean; disabled?: boolean }>()
const { t } = useI18n()
const playing = ref(false)
const failed = ref(false)
let audio: HTMLAudioElement | null = null

// WeChat-like: bubble width grows with duration (60 px … 200 px).
const width = computed(() => {
  const s = Math.min(60, Math.max(1, props.durationMs / 1000))
  return `${Math.round(64 + (s / 60) * 136)}px`
})
const label = computed(() => formatVoiceDuration(props.durationMs))

function stop() {
  if (audio) {
    audio.pause()
    audio.currentTime = 0
  }
  playing.value = false
}

async function toggle() {
  if (!props.src || props.disabled) return
  if (playing.value) {
    stop()
    releaseAudio(stop)
    return
  }
  if (!audio) {
    audio = new Audio(props.src)
    audio.preload = 'none'
    audio.onended = () => {
      playing.value = false
      releaseAudio(stop)
    }
    audio.onerror = () => {
      failed.value = true
      playing.value = false
    }
  }
  claimAudio(stop)
  try {
    failed.value = false
    await audio.play()
    playing.value = true
  } catch {
    failed.value = true
    playing.value = false
  }
}

onBeforeUnmount(() => {
  stop()
  releaseAudio(stop)
  audio = null
})
</script>

<template>
  <button
    type="button"
    class="gc-voice"
    :class="{ self, playing, failed }"
    :style="{ width }"
    :aria-label="`${t('chat.voice.play')} ${label}`"
    :aria-pressed="playing"
    @click="toggle"
  >
    <svg class="gc-voice-icon" viewBox="0 0 24 24" aria-hidden="true">
      <path class="w1" d="M8.5 12a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0Z" />
      <path class="w2" d="M10.8 7.6a6 6 0 0 1 0 8.8" fill="none" stroke-width="1.8" stroke-linecap="round" />
      <path class="w3" d="M14 4.6a10.2 10.2 0 0 1 0 14.8" fill="none" stroke-width="1.8" stroke-linecap="round" />
    </svg>
    <span class="gc-voice-len">{{ label }}</span>
    <span v-if="failed" class="gc-voice-err" aria-hidden="true">!</span>
  </button>
</template>

<style scoped>
.gc-voice {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 40px;
  padding: 0 12px;
  border: 0;
  border-radius: var(--gc-bubble-radius, 14px);
  background: var(--gc-other-bg, #fff);
  color: var(--gc-text, #1f1f1f);
  font: inherit;
  font-size: 15px;
  cursor: pointer;
  max-width: 100%;
}
.gc-voice.self {
  flex-direction: row-reverse;
  background: var(--gc-self-bg, #ffe14d);
  color: var(--gc-self-fg, #1f1f1f);
}
.gc-voice.self .gc-voice-icon {
  transform: scaleX(-1);
}
.gc-voice-icon {
  width: 22px;
  height: 22px;
  flex: none;
  fill: currentColor;
  stroke: currentColor;
}
.gc-voice.playing .w2 {
  animation: gc-wave 1s infinite steps(1);
}
.gc-voice.playing .w3 {
  animation: gc-wave 1s infinite steps(1) 0.33s;
}
@keyframes gc-wave {
  0% {
    opacity: 0.15;
  }
  50% {
    opacity: 1;
  }
}
.gc-voice-len {
  font-variant-numeric: tabular-nums;
}
.gc-voice-err {
  margin-inline-start: auto;
  color: var(--gc-danger, #e5484d);
  font-weight: 700;
}
</style>
