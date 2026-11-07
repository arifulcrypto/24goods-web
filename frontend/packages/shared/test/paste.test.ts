import { ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'
import { extractPastedImages, usePasteImage } from '../src/media/paste'

function pasteEvent(items: { kind: string; type: string; file?: File }[], files: File[] = []) {
  const e = {
    clipboardData: {
      items: items.map((i) => ({ kind: i.kind, type: i.type, getAsFile: () => i.file ?? null })),
      files,
    },
    defaultPrevented: false,
    preventDefault() {
      this.defaultPrevented = true
    },
  }
  return e as unknown as ClipboardEvent & { defaultPrevented: boolean }
}

describe('extractPastedImages', () => {
  it('turns image items into named Files', () => {
    const png = new File([new Uint8Array([1, 2, 3])], 'image.png', { type: 'image/png' })
    const out = extractPastedImages(pasteEvent([{ kind: 'file', type: 'image/png', file: png }]))
    expect(out).toHaveLength(1)
    expect(out[0].name).toMatch(/^pasted-\d+-0\.png$/)
    expect(out[0].type).toBe('image/png')
  })
  it('ignores text and non-image files', () => {
    const pdf = new File(['x'], 'a.pdf', { type: 'application/pdf' })
    expect(
      extractPastedImages(
        pasteEvent([
          { kind: 'string', type: 'text/plain' },
          { kind: 'file', type: 'application/pdf', file: pdf },
        ]),
      ),
    ).toEqual([])
  })
  it('falls back to clipboardData.files', () => {
    const jpg = new File(['x'], 'shot.jpg', { type: 'image/jpeg' })
    expect(extractPastedImages(pasteEvent([], [jpg]))).toEqual([jpg])
  })
  it('handles a missing clipboardData', () => {
    expect(extractPastedImages({ clipboardData: null })).toEqual([])
  })
})

describe('usePasteImage', () => {
  it('calls back for images (and prevents default) but not for text', () => {
    const target = new EventTarget()
    const cb = vi.fn()
    usePasteImage(target, cb)
    const text = pasteEvent([{ kind: 'string', type: 'text/plain' }])
    const tev = Object.assign(new Event('paste', { cancelable: true }), { clipboardData: text.clipboardData })
    target.dispatchEvent(tev)
    expect(cb).not.toHaveBeenCalled()
    expect(tev.defaultPrevented).toBe(false)
    const png = new File(['x'], 'image.png', { type: 'image/png' })
    const img = pasteEvent([{ kind: 'file', type: 'image/png', file: png }])
    const ev = Object.assign(new Event('paste', { cancelable: true }), { clipboardData: img.clipboardData })
    target.dispatchEvent(ev)
    expect(cb).toHaveBeenCalledOnce()
    expect(ev.defaultPrevented).toBe(true)
  })
  it('respects enabled=false and dispose', () => {
    const target = new EventTarget()
    const cb = vi.fn()
    const enabled = ref(false)
    const dispose = usePasteImage(target, cb, enabled)
    const png = new File(['x'], 'image.png', { type: 'image/png' })
    const mk = () =>
      Object.assign(new Event('paste'), { clipboardData: pasteEvent([{ kind: 'file', type: 'image/png', file: png }]).clipboardData })
    target.dispatchEvent(mk())
    expect(cb).not.toHaveBeenCalled()
    enabled.value = true
    target.dispatchEvent(mk())
    expect(cb).toHaveBeenCalledOnce()
    dispose()
    target.dispatchEvent(mk())
    expect(cb).toHaveBeenCalledOnce()
  })
})
