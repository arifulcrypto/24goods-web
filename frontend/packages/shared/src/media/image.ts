import { compressUploads, lowData } from './preferences'
/**
 * Client-side image downscale before upload (≤ 2048 px on the long edge).
 * The server still validates magic bytes, re-encodes and strips EXIF — this is only for speed.
 */

export const IMAGE_MAX_DIM = 2048
export const IMAGE_MAX_BYTES = 8 * 1024 * 1024
const ACCEPTED = ['image/jpeg', 'image/png', 'image/webp', 'image/gif']

export function isAcceptedImage(file: Blob): boolean {
  return ACCEPTED.includes(file.type)
}

/** Fit (w,h) inside a max×max box keeping the aspect ratio. */
export function fitWithin(w: number, h: number, max: number = IMAGE_MAX_DIM): { width: number; height: number } {
  if (w <= max && h <= max) return { width: w, height: h }
  const r = Math.min(max / w, max / h)
  return { width: Math.max(1, Math.round(w * r)), height: Math.max(1, Math.round(h * r)) }
}

interface Decoded {
  source: CanvasImageSource
  width: number
  height: number
  close(): void
}

async function decode(file: Blob): Promise<Decoded> {
  if (typeof createImageBitmap === 'function') {
    try {
      const bmp = await createImageBitmap(file, { imageOrientation: 'from-image' } as ImageBitmapOptions)
      return { source: bmp, width: bmp.width, height: bmp.height, close: () => bmp.close() }
    } catch {
      /* fall through to <img> */
    }
  }
  const url = URL.createObjectURL(file)
  try {
    const img = new Image()
    img.decoding = 'async'
    img.src = url
    await img.decode()
    return { source: img, width: img.naturalWidth, height: img.naturalHeight, close: () => undefined }
  } finally {
    URL.revokeObjectURL(url)
  }
}

export interface CompressOptions {
  maxDim?: number
  quality?: number
  /** Skip re-encoding if already small enough and within maxDim. */
  skipBelowBytes?: number
}

export interface CompressedImage {
  blob: Blob
  width: number
  height: number
}

export async function compressImage(file: Blob, o: CompressOptions = {}): Promise<CompressedImage> {
  if (!compressUploads.value) return { blob: file, width: 0, height: 0 }
  const maxDim = o.maxDim ?? (lowData() ? 1280 : IMAGE_MAX_DIM)
  const quality = o.quality ?? (lowData() ? 0.72 : 0.85)
  const skipBelow = o.skipBelowBytes ?? 1024 * 1024
  // GIFs: server takes the first frame; canvas would do the same but we let the server do it.
  if (file.type === 'image/gif') return { blob: file, width: 0, height: 0 }
  const d = await decode(file)
  try {
    const { width, height } = fitWithin(d.width, d.height, maxDim)
    if (width === d.width && height === d.height && file.size <= skipBelow) {
      return { blob: file, width, height }
    }
    const canvas = document.createElement('canvas')
    canvas.width = width
    canvas.height = height
    const ctx = canvas.getContext('2d')
    if (!ctx) return { blob: file, width: d.width, height: d.height }
    ctx.imageSmoothingQuality = 'high'
    ctx.drawImage(d.source, 0, 0, width, height)
    const type = file.type === 'image/png' || file.type === 'image/webp' ? 'image/webp' : 'image/jpeg'
    const blob = await new Promise<Blob | null>((res) => canvas.toBlob(res, type, quality))
    if (!blob || blob.size >= file.size) return { blob: file, width, height }
    return { blob, width, height }
  } finally {
    d.close()
  }
}

export function imageExtension(mime: string): string {
  if (mime === 'image/png') return 'png'
  if (mime === 'image/webp') return 'webp'
  if (mime === 'image/gif') return 'gif'
  return 'jpg'
}
