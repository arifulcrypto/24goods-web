import { ref } from 'vue'

/** Shared 30 s clock for live relative times in chat (one interval for all bubbles). */
export const nowTick = ref(Date.now())
if (typeof window !== 'undefined') setInterval(() => (nowTick.value = Date.now()), 30_000)
