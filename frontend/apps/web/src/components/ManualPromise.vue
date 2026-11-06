<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { manualHours, manualNote } from '@24goods/shared'
import { useSiteStore } from '../stores/site'

/** review-v5 §11: "Manual · ships within {hours} h" + the site note (hours substituted client-side). */
const props = defineProps<{ eta?: number | null; compact?: boolean }>()
const { t } = useI18n()
const site = useSiteStore()
const hours = computed(() => manualHours(props.eta, site.manualDefaultHours))
const note = computed(() => manualNote(site.manualNoteRaw, hours.value))
</script>

<template>
  <div class="manual-promise">
    <span class="badge-manual">{{ t('manual.eta', { hours }) }}</span>
    <p v-if="!compact && note" class="muted note">{{ note }}</p>
  </div>
</template>

<style scoped>
.note {
  margin: 4px 0 0;
  font-size: 12px;
  line-height: 1.4;
}
</style>
