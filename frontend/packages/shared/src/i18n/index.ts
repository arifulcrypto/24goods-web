import en from './en'
import zhCN from './zh-CN'

type Widen<T> = T extends string ? string : { [K in keyof T]: Widen<T[K]> }
/** Shape of the shared bundle (keys from en, string values). */
export type MessageSchema = Widen<typeof en>

export const sharedMessages = { en: en as MessageSchema, 'zh-CN': zhCN }

export type MessageTree = { [k: string]: string | MessageTree }

/** Deep-merge message trees (later wins). */
export function mergeMessageTrees(...trees: MessageTree[]): MessageTree {
  const out: MessageTree = {}
  for (const t of trees) {
    for (const [k, v] of Object.entries(t)) {
      const cur = out[k]
      if (typeof v === 'object' && v && typeof cur === 'object' && cur) out[k] = mergeMessageTrees(cur, v)
      else if (typeof v === 'object' && v) out[k] = mergeMessageTrees(v)
      else out[k] = v
    }
  }
  return out
}

/** Flatten to dotted keys: { a: { b: 'x' } } → { 'a.b': 'x' } */
export function flattenMessages(tree: MessageTree, prefix = ''): Record<string, string> {
  const out: Record<string, string> = {}
  for (const [k, v] of Object.entries(tree)) {
    const key = prefix ? `${prefix}.${k}` : k
    if (typeof v === 'string') out[key] = v
    else Object.assign(out, flattenMessages(v, key))
  }
  return out
}

/** Keys present in `a` but missing in `b`, and vice versa. */
export function diffMessageKeys(a: MessageTree, b: MessageTree): { missing: string[]; extra: string[] } {
  const fa = Object.keys(flattenMessages(a))
  const fb = new Set(Object.keys(flattenMessages(b)))
  const sa = new Set(fa)
  return {
    missing: fa.filter((k) => !fb.has(k)).sort(),
    extra: [...fb].filter((k) => !sa.has(k)).sort(),
  }
}

/** Placeholder names used in a message, e.g. "Hi {name}" → ["name"]. */
export function placeholders(msg: string): string[] {
  return Array.from(msg.matchAll(/\{\s*([a-zA-Z_][\w]*)\s*\}/g), (m) => m[1]!).sort()
}
