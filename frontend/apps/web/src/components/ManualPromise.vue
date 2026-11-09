<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { manualHours, manualNote } from '@24goods/shared'
import { useSiteStore } from '../stores/site'

/**
 * review-v5 §11: "Manual · ships within {hours} h" + the site note (hours substituted client-side).
 * `compact` = badge only; `noteOnly` = the explanation only, for a card that already shows the
 * badge (issue 48 §2.1: the product page used to print the badge twice in one card).
 */
const props = defineProps<{ eta?: number | null; compact?: boolean; noteOnly?: boolean }>()
const { t } = useI18n()
const site = useSiteStore()
const hours = computed(() => manualHours(props.eta, site.manualDefaultHours))
const note = computed(() => manualNote(site.manualNoteRaw, hours.value))
</script>

<template>
  <div v-if="!noteOnly || note" class="manual-promise">
    <span v-if="!noteOnly" class="badge-manual">{{ t('manual.eta', { hours }) }}</span>
    <p v-if="!compact && note" class="muted note">{{ note }}</p>
  </div>
</template>

<style scoped>
.note {
  margin: 4px 0 0;
  font-size: 12px;
  line-height: 1.4;
}
.note:first-child {
  margin-top: 0;
}
</style>
