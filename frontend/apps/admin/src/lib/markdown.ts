/**
 * Tiny markdown -> HTML for the editor PREVIEW only. Input is HTML-escaped first, only http(s) and
 * `/api/v1/files/<id>` URLs are allowed, and the result is rendered through the shared SafeHtml
 * (which sanitises again). The server render (core/markdown.py) stays the source of truth.
 */
const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

export function safeUrl(u: string): string | null {
  const v = u.trim()
  if (/^https?:\/\/[^\s"'<>]+$/i.test(v)) return v
  if (/^\/api\/v1\/files\/[0-9a-zA-Z-]+$/.test(v)) return v
  return null
}

function inline(raw: string): string {
  let s = esc(raw)
  // images first (so the link rule does not eat them); url was escaped, undo &amp; for matching
  s = s.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (_m, alt: string, url: string) => {
    const u = safeUrl(url.replace(/&amp;/g, '&'))
    return u ? `<img src="${esc(u)}" alt="${alt}" loading="lazy">` : _m
  })
  s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_m, text: string, url: string) => {
    const u = safeUrl(url.replace(/&amp;/g, '&'))
    return u ? `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${text}</a>` : _m
  })
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>')
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  s = s.replace(/(^|[^*])\*([^*\s][^*]*)\*/g, '$1<em>$2</em>')
  return s
}

export function renderMarkdownPreview(src: string): string {
  const out: string[] = []
  let list: 'ul' | 'ol' | null = null
  let para: string[] = []
  const flushPara = () => {
    if (para.length) out.push(`<p>${para.map(inline).join('<br>')}</p>`)
    para = []
  }
  const closeList = () => {
    if (list) out.push(`</${list}>`)
    list = null
  }
  for (const line of src.replace(/\r\n?/g, '\n').split('\n')) {
    const h = /^(#{1,4})\s+(.*)$/.exec(line)
    const ul = /^\s*[-*]\s+(.*)$/.exec(line)
    const ol = /^\s*\d+[.)]\s+(.*)$/.exec(line)
    if (h) {
      flushPara()
      closeList()
      out.push(`<h${h[1]!.length}>${inline(h[2]!)}</h${h[1]!.length}>`)
    } else if (ul || ol) {
      flushPara()
      const kind = ul ? 'ul' : 'ol'
      if (list !== kind) {
        closeList()
        out.push(`<${kind}>`)
        list = kind
      }
      out.push(`<li>${inline((ul ?? ol)![1]!)}</li>`)
    } else if (!line.trim()) {
      flushPara()
      closeList()
    } else {
      closeList()
      para.push(line)
    }
  }
  flushPara()
  closeList()
  return out.join('\n')
}

/** Insert/wrap helpers used by the toolbar; pure so they are testable. */
export interface Edit {
  text: string
  selStart: number
  selEnd: number
}
export function wrapSelection(text: string, start: number, end: number, before: string, after: string, placeholder: string): Edit {
  const sel = text.slice(start, end) || placeholder
  const next = text.slice(0, start) + before + sel + after + text.slice(end)
  return { text: next, selStart: start + before.length, selEnd: start + before.length + sel.length }
}
export function prefixLines(text: string, start: number, end: number, prefix: string): Edit {
  const ls = text.lastIndexOf('\n', start - 1) + 1
  let le = text.indexOf('\n', end)
  if (le === -1) le = text.length
  const block = text
    .slice(ls, le)
    .split('\n')
    .map((l) => prefix + l)
    .join('\n')
  return { text: text.slice(0, ls) + block + text.slice(le), selStart: ls, selEnd: ls + block.length }
}
export function insertAt(text: string, pos: number, snippet: string): Edit {
  return { text: text.slice(0, pos) + snippet + text.slice(pos), selStart: pos + snippet.length, selEnd: pos + snippet.length }
}
