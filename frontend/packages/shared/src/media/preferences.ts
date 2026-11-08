import { ref } from 'vue'
export type ImageQuality = 'auto' | 'low' | 'high'
interface Connection { saveData?: boolean; effectiveType?: string; type?: string }
function read(key: string): string | null { try { return localStorage.getItem(key) } catch { return null } }
export const imageQuality = ref<ImageQuality>((['auto', 'low', 'high'].includes(read('24goods.imageQuality') ?? '') ? read('24goods.imageQuality') : 'auto') as ImageQuality)
export const compressUploads = ref(read('24goods.compressUploads') !== 'false')
export function setMediaPreference(quality: ImageQuality, compress: boolean): void {
  imageQuality.value = quality; compressUploads.value = compress
  try { localStorage.setItem('24goods.imageQuality', quality); localStorage.setItem('24goods.compressUploads', String(compress)) } catch { /* optional storage */ }
}
export function lowData(): boolean {
  if (imageQuality.value !== 'auto') return imageQuality.value === 'low'
  const c = (typeof navigator === 'undefined' ? undefined : (navigator as Navigator & { connection?: Connection }).connection)
  // Browsers without Network Information get a conservative preview; original opens on demand.
  return !c || c.saveData === true || c.type === 'cellular' || ['slow-2g', '2g', '3g'].includes(c.effectiveType ?? '')
}
export function previewImageUrl(url: string | null): string | null {
  if (!url || !lowData()) return url
  try {
    const base=globalThis.location?.href ?? 'http://localhost/'
    const parsed=new URL(url,base)
    if(parsed.origin!==new URL(base).origin||!parsed.pathname.startsWith('/api/v1/files/'))return url
    parsed.searchParams.set('variant','small')
    return parsed.toString()
  } catch {return url}
}
