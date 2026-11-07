import { onBeforeUnmount, unref, watch, type Ref } from 'vue'

/** Image files on a paste event's clipboard (screenshots), as `pasted-<ts>.png`-style Files. */
export function extractPastedImages(e: Pick<ClipboardEvent, 'clipboardData'>): File[] {
  const dt = e.clipboardData
  if (!dt) return []
  const out: File[] = []
  const ts = Date.now()
  for (const item of Array.from(dt.items ?? [])) {
    if (item.kind !== 'file' || !item.type.startsWith('image/')) continue
    const f = item.getAsFile()
    if (!f) continue
    const ext = (f.type.split('/')[1] || 'png').replace('jpeg', 'jpg').replace(/[^a-z0-9]/gi, '') || 'png'
    out.push(new File([f], f.name && f.name !== 'image.png' ? f.name : `pasted-${ts}-${out.length}.${ext}`, { type: f.type }))
  }
  if (out.length === 0) {
    // some browsers expose pasted files only through `files`
    for (const f of Array.from(dt.files ?? [])) {
      if (f.type.startsWith('image/')) out.push(f)
    }
  }
  return out
}

export interface PasteTarget {
  addEventListener(type: 'paste', fn: (e: Event) => void): void
  removeEventListener(type: 'paste', fn: (e: Event) => void): void
}

/**
 * Listen for `paste` on `target` (an element, or `window` while a dialog is open via `enabled`)
 * and hand pasted image files to `onFiles`. Text pastes are untouched (no preventDefault, no call).
 * The files still go through the caller's normal upload flow; the server validates by magic bytes.
 */
export function usePasteImage(
  target: Ref<PasteTarget | null | undefined> | PasteTarget | null | undefined,
  onFiles: (files: File[]) => void,
  enabled: Ref<boolean> | boolean = true,
): () => void {
  const handler = (e: Event) => {
    if (!unref(enabled)) return
    const files = extractPastedImages(e as ClipboardEvent)
    if (files.length === 0) return
    e.preventDefault()
    onFiles(files)
  }
  let attached: PasteTarget | null = null
  const detach = () => {
    attached?.removeEventListener('paste', handler)
    attached = null
  }
  const stop = watch(
    () => unref(target) ?? null,
    (el) => {
      detach()
      if (el) {
        el.addEventListener('paste', handler)
        attached = el
      }
    },
    { immediate: true, flush: 'post' },
  )
  const dispose = () => {
    stop()
    detach()
  }
  onBeforeUnmount(dispose)
  return dispose
}
