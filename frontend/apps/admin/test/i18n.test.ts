import { describe, expect, it } from 'vitest'
import { diffMessageKeys, flattenMessages, placeholders, type MessageTree } from '@24goods/shared'
import en from '../src/i18n/en'
import zhCN from '../src/i18n/zh-CN'

const a = en as unknown as MessageTree
const b = zhCN as unknown as MessageTree

describe('admin i18n', () => {
  it('zh-CN has exactly the keys of en', () => {
    expect(diffMessageKeys(a, b)).toEqual({ missing: [], extra: [] })
  })
  it('no empty strings, same placeholders', () => {
    const fa = flattenMessages(a)
    const fb = flattenMessages(b)
    for (const [k, v] of Object.entries(fa)) {
      expect(v.trim(), k).not.toBe('')
      expect(fb[k]?.trim(), k).not.toBe('')
      expect(placeholders(fb[k] ?? ''), k).toEqual(placeholders(v))
    }
  })
  it('no raw @ (vue-i18n linked messages)', () => {
    for (const [k, v] of Object.entries({ ...flattenMessages(a), ...flattenMessages(b) })) {
      expect(/@/.test(v.replace(/\{'@'\}/g, '')), k).toBe(false)
    }
  })
})
describe('admin i18n messages compile with vue-i18n', () => {
  it('every merged message (shared + app, both locales) renders without compile errors', async () => {
    const { createI18n } = await import('vue-i18n')
    const { messages } = await import('../src/i18n')
    const errors: string[] = []
    for (const loc of ['en', 'zh-CN'] as const) {
      const i18n = createI18n({
        legacy: false,
        locale: loc,
        messages: { [loc]: messages[loc] },
        missingWarn: false,
        fallbackWarn: false,
        warnHtmlMessage: false,
        messageCompiler: undefined,
      })
      const flat = flattenMessages(messages[loc])
      for (const key of Object.keys(flat)) {
        const params = Object.fromEntries(placeholders(flat[key]!).map((p) => [p, 'X']))
        try {
          const out = i18n.global.t(key, params, 2)
          if (typeof out !== 'string' || out === key) errors.push(`${loc}:${key} → ${String(out)}`)
        } catch (e) {
          errors.push(`${loc}:${key} threw ${(e as Error).message}`)
        }
      }
    }
    expect(errors).toEqual([])
  })
})
