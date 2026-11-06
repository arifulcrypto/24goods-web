/**
 * Pure tokenizer for "smart text" (review-v4 §7). No DOM, no HTML: callers render the segments as
 * Vue nodes (no raw HTML). Every pattern is linear-time (no nested/ambiguous quantifiers).
 */
export type SegmentKind = 'text' | 'url' | 'email' | 'order_no' | 'number' | 'code'

export interface Segment {
  kind: SegmentKind
  /** Text as written (what a chip shows / what copy copies). */
  text: string
  /** url only: normalised absolute http(s) URL. */
  href?: string
  /** url only: hostname for the chip label. */
  host?: string
}

export interface LinkifyOptions {
  /** Treat `backtick` spans as copyable code chips (popup prompts only). */
  code?: boolean
}

/** Input is clamped so a hostile 1 MB message cannot stall the UI thread. */
export const LINKIFY_MAX_LEN = 20_000

// http(s):// then anything that is not whitespace, angle brackets, backtick, or CJK letters/punctuation.
const URL_RE = /https?:\/\/[^\s<>`　-〿一-鿿＀-￯]+/gi
const TRAIL = new Set(['.', ',', ';', ':', '!', '?', ')', ']', '}', '"', "'", '】', '」', '）', '。', '，'])
// Alternation order = priority at the same start index: email, order number, long number.
const SMART_RE =
  /([A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63})+)|(\bG[A-Z2-7]{12}\b)|(\d(?:[ -]?\d){5,})/g
const NUMBER_MAX = 32

function push(out: Segment[], seg: Segment): void {
  if (!seg.text) return
  const last = out[out.length - 1]
  if (seg.kind === 'text' && last && last.kind === 'text') last.text += seg.text
  else out.push(seg)
}

function parseHttpUrl(raw: string): { href: string; host: string } | null {
  try {
    const u = new URL(raw)
    if ((u.protocol !== 'http:' && u.protocol !== 'https:') || !u.hostname) return null
    if (u.username || u.password) return null
    return { href: u.toString(), host: u.hostname }
  } catch {
    return null
  }
}

function smart(text: string, out: Segment[]): void {
  let pos = 0
  SMART_RE.lastIndex = 0
  for (let m = SMART_RE.exec(text); m; m = SMART_RE.exec(text)) {
    const [whole, email, order, num] = m
    if (num !== undefined && whole.length > NUMBER_MAX) {
      // too long to be a phone / account number: leave as plain text
      continue
    }
    push(out, { kind: 'text', text: text.slice(pos, m.index) })
    push(out, { kind: email ? 'email' : order ? 'order_no' : 'number', text: whole })
    pos = m.index + whole.length
  }
  push(out, { kind: 'text', text: text.slice(pos) })
}

function urls(text: string, out: Segment[]): void {
  let pos = 0
  URL_RE.lastIndex = 0
  for (let m = URL_RE.exec(text); m; m = URL_RE.exec(text)) {
    let raw = m[0]
    while (raw.length > 0 && TRAIL.has(raw[raw.length - 1]!)) raw = raw.slice(0, -1)
    const parsed = raw.length > 'https://'.length ? parseHttpUrl(raw) : null
    if (!parsed) continue
    smart(text.slice(pos, m.index), out)
    push(out, { kind: 'url', text: raw, href: parsed.href, host: parsed.host })
    pos = m.index + raw.length
    URL_RE.lastIndex = pos
  }
  smart(text.slice(pos), out)
}

export function linkify(input: string | null | undefined, opts: LinkifyOptions = {}): Segment[] {
  const out: Segment[] = []
  if (!input) return out
  const text = input.length > LINKIFY_MAX_LEN ? input.slice(0, LINKIFY_MAX_LEN) : input
  if (!opts.code) {
    urls(text, out)
  } else {
    let pos = 0
    for (;;) {
      const open = text.indexOf('`', pos)
      if (open < 0) break
      const close = text.indexOf('`', open + 1)
      if (close < 0) break
      const inner = text.slice(open + 1, close)
      if (!inner || inner.includes('\n')) {
        // not a code span: keep the backtick as text and continue after it
        urls(text.slice(pos, open + 1), out)
        pos = open + 1
        continue
      }
      urls(text.slice(pos, open), out)
      push(out, { kind: 'code', text: inner })
      pos = close + 1
    }
    urls(text.slice(pos), out)
  }
  if (input.length > LINKIFY_MAX_LEN) push(out, { kind: 'text', text: input.slice(LINKIFY_MAX_LEN) })
  return out
}
