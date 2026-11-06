import type { ImportImage } from '../api-v5'

/** Escape markdown image alt text (`[`, `]`, newlines). */
export const cleanAlt = (s: string): string => s.replace(/[[\]\r\n]+/g, ' ').trim()

/**
 * Rewrite image URLs in a markdown description using `map` (original url → our url). Images that are
 * not in the map (unselected / failed to fetch) are dropped so no third-party URL is hot-linked.
 * Selected images that the description does not mention are appended at the end.
 */
export function rewriteImages(md: string, map: Map<string, string>, selected: ImportImage[]): string {
  const used = new Set<string>()
  const altBy = new Map(selected.map((i) => [i.url, i.alt]))
  let out = md.replace(/!\[([^\]]*)\]\(\s*([^)\s]+)(?:\s+"[^"]*")?\s*\)/g, (_m, alt: string, url: string) => {
    const ours = map.get(url)
    if (!ours) return ''
    used.add(url)
    return `![${cleanAlt(altBy.get(url) || alt)}](${ours})`
  })
  const extra = selected.filter((i) => map.has(i.url) && !used.has(i.url))
  if (extra.length) out = `${out.trimEnd()}\n\n${extra.map((i) => `![${cleanAlt(i.alt)}](${map.get(i.url)!})`).join('\n\n')}`
  return out.replace(/\n{3,}/g, '\n\n').trim()
}

/** First valid http(s) base for resolving relative links in pasted HTML. */
export function pickClipboard(dt: Pick<DataTransfer, 'getData'> | null | undefined): { html?: string; text?: string } {
  const html = dt?.getData('text/html') ?? ''
  if (html.trim()) return { html }
  const text = dt?.getData('text/plain') ?? ''
  return text.trim() ? { text } : {}
}
