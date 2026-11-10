import { ref } from 'vue'

/**
 * Drag-to-reorder for an Element Plus table without extra dependencies. Put `draggable="true"` and
 * `@dragstart="start(i, $event)"` on a grip element in each row, and `@dragover.prevent` +
 * `@drop="drop"` on a wrapper around the table. `move(i, ±1)` backs the keyboard / touch buttons.
 * `onMove(from, to)` receives indexes into the displayed list.
 */
export function useRowReorder(onMove: (from: number, to: number) => void) {
  const dragFrom = ref<number | null>(null)
  function start(i: number, ev: DragEvent) {
    dragFrom.value = i
    if (ev.dataTransfer) {
      ev.dataTransfer.effectAllowed = 'move'
      ev.dataTransfer.setData('text/plain', String(i))
    }
  }
  function drop(ev: DragEvent) {
    const from = dragFrom.value
    dragFrom.value = null
    const tr = (ev.target as HTMLElement | null)?.closest('tbody tr')
    if (from === null || !tr?.parentElement) return
    const to = Array.from(tr.parentElement.children).indexOf(tr)
    if (to >= 0 && to !== from) onMove(from, to)
  }
  function move(i: number, delta: number) {
    onMove(i, i + delta)
  }
  return { start, drop, move }
}

/** `list` with the element at `from` moved to `to` (new array; out-of-range is a no-op). */
export function moved<T>(list: readonly T[], from: number, to: number): T[] {
  if (from === to || from < 0 || to < 0 || from >= list.length || to >= list.length) return [...list]
  const next = [...list]
  const [item] = next.splice(from, 1)
  next.splice(to, 0, item)
  return next
}
