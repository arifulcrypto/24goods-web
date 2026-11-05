<script setup lang="ts">
/**
 * The ONLY place that renders server-sanitised HTML (`*_html` fields: usage_html, body_html,
 * announcement body). The server already ran markdown-it (HTML off) + an nh3 allowlist; this
 * re-sanitises client-side as defence in depth (scripts, styles, frames, on* handlers, unsafe URLs).
 */
import { computed } from 'vue'
import { sanitizeHtml } from '../sanitize'

const props = defineProps<{ html: string | null | undefined }>()
const clean = computed(() => sanitizeHtml(props.html ?? ''))
</script>

<template>
  <div class="safe-html" v-html="clean" />
</template>
