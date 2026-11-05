import { describe, expect, it } from 'vitest'
import { API_ERROR_CODES, CLIENT_ERROR_CODES, diffMessageKeys, flattenMessages, placeholders, sharedMessages } from '../src'
import type { MessageTree } from '../src'

const en = sharedMessages.en as unknown as MessageTree
const zh = sharedMessages['zh-CN'] as unknown as MessageTree

describe('shared i18n bundles', () => {
  it('zh-CN has exactly the same keys as en', () => {
    expect(diffMessageKeys(en, zh)).toEqual({ missing: [], extra: [] })
  })
  it('no empty translations and identical placeholders', () => {
    const fe = flattenMessages(en)
    const fz = flattenMessages(zh)
    for (const [k, v] of Object.entries(fe)) {
      expect(v.trim(), k).not.toBe('')
      expect(fz[k]?.trim(), k).not.toBe('')
      expect(placeholders(fz[k] ?? ''), k).toEqual(placeholders(v))
    }
  })
  it('has a translation for every contract + client error code', () => {
    const fe = flattenMessages(en)
    for (const code of [...API_ERROR_CODES, ...CLIENT_ERROR_CODES]) expect(fe[`errors.${code}`], code).toBeTruthy()
  })
  it('does not contain raw @ (vue-i18n linked-message syntax)', () => {
    for (const [k, v] of Object.entries({ ...flattenMessages(en), ...flattenMessages(zh) })) {
      expect(/@(?!')/.test(v.replace(/\{'@'\}/g, '')), k).toBe(false)
    }
  })
})
