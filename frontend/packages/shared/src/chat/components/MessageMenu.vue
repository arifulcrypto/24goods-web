<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

export interface MenuItem {
  key: string
  /** Decorative glyph (no asset files). */
  icon: string
  label: string
  danger?: boolean
}

const props = defineProps<{ open: boolean; items: MenuItem[]; x?: number; y?: number; label?: string }>()
const emit = defineEmits<{ select: [key: string]; close: [] }>()

/** Bottom sheet on phones, popover next to the pointer on desktop. */
const mobile = computed(() => typeof window !== 'undefined' && !!window.matchMedia?.('(max-width: 640px), (pointer: coarse)').matches)
const panel = ref<HTMLElement | null>(null)
const pos = ref<{ left: string; top: string }>({ left: '0px', top: '0px' })

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    e.stopPropagation()
    emit('close')
  }
}
watch(
  () => props.open,
  async (v) => {
    if (v) {
      document.addEventListener('keydown', onKey, true)
      await nextTick()
      if (!mobile.value) {
        const w = panel.value?.offsetWidth ?? 220
        const h = panel.value?.offsetHeight ?? 220
        const left = Math.min(Math.max(8, (props.x ?? 0) - 8), window.innerWidth - w - 8)
        const top = Math.min(Math.max(8, props.y ?? 0), window.innerHeight - h - 8)
        pos.value = { left: `${left}px`, top: `${top}px` }
      }
      panel.value?.querySelector<HTMLElement>('button')?.focus()
    } else document.removeEventListener('keydown', onKey, true)
  },
  { immediate: true },
)
onBeforeUnmount(() => document.removeEventListener('keydown', onKey, true))
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="gc-menu-backdrop" :class="{ sheet: mobile }" @click.self="emit('close')" @contextmenu.prevent.self="emit('close')">
      <div ref="panel" class="gc-menu" :class="{ sheet: mobile }" role="menu" :aria-label="label" :style="mobile ? undefined : pos">
        <button v-for="it in items" :key="it.key" type="button" role="menuitem" class="gc-menu-row" :class="{ danger: it.danger }" @click="emit('select', it.key)">
          <span class="gc-menu-ico" aria-hidden="true">{{ it.icon }}</span>
          <span class="gc-menu-label">{{ it.label }}</span>
        </button>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.gc-menu-backdrop { position: fixed; inset: 0; z-index: 3000; background: transparent }
.gc-menu-backdrop.sheet { background: rgb(0 0 0 / 0.35); display: flex; align-items: flex-end }
.gc-menu { position: fixed; display: flex; flex-direction: column; min-width: 200px; max-width: min(320px, calc(100vw - 16px)); padding: 6px 0; background: var(--gc-panel, #fff); color: var(--gc-text, #1f2329); border: 1px solid rgb(0 0 0 / 0.12); border-radius: 12px; box-shadow: 0 8px 28px rgb(0 0 0 / 0.22) }
.gc-menu.sheet { position: static; width: 100%; max-width: none; min-width: 0; border-radius: 16px 16px 0 0; padding: 8px 0 calc(8px + env(safe-area-inset-bottom, 0px)) }
.gc-menu-row { display: flex; align-items: center; gap: 14px; width: 100%; min-height: 44px; padding: 0 18px; border: 0; background: transparent; color: inherit; font: inherit; text-align: start; cursor: pointer }
.gc-menu-row:hover, .gc-menu-row:focus-visible { background: rgb(127 127 127 / 0.14); outline: none }
.gc-menu-row.danger { color: #d03050 }
.gc-menu-ico { width: 22px; text-align: center; font-size: 17px; flex: none }
.gc-menu-label { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap }
</style>
