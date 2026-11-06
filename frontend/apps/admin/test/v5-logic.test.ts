import { describe, expect, it } from 'vitest'
import { marginMinor, marginPct, minorToYuan, pctToPermille, permilleToPct, roundUp, suggestPrice, yuanToMinor } from '../src/lib/money'
import { secretChange, secretFromServer, secretLabel } from '../src/lib/secret'
import { jsonDiff } from '../src/lib/diff'
import { cleanAlt, pickClipboard, rewriteImages } from '../src/lib/importDraft'
import { emptyPromoForm, formFromPromo, promoToInput, promoValueText, randomCode } from '../src/lib/promo'
import { insertPlaceholder } from '../src/lib/emailTemplates'
import { auditSides } from '../src/lib/auditRow'

describe('money', () => {
  it('yuan <-> minor', () => {
    expect(yuanToMinor('19.9')).toBe(1990)
    expect(yuanToMinor('0.05')).toBe(5)
    expect(yuanToMinor('1.234')).toBeNull()
    expect(yuanToMinor('-1')).toBeNull()
    expect(yuanToMinor('')).toBeNull()
    expect(minorToYuan(1990)).toBe('19.90')
    expect(minorToYuan(5)).toBe('0.05')
  })
  it('percent <-> permille', () => {
    expect(pctToPermille(12.5)).toBe(125)
    expect(permilleToPct(200)).toBe(20)
  })
  it('suggested price rounds up', () => {
    expect(roundUp(1234, 10)).toBe(1240)
    expect(roundUp(1230, 10)).toBe(1230)
    expect(roundUp(1234, 1)).toBe(1234)
    expect(suggestPrice(10000, 'percent', 200, 10)).toBe(12000)
    expect(suggestPrice(1001, 'percent', 200, 10)).toBe(1210) // 1001 + ceil(200.2)=1202 -> 1210
    expect(suggestPrice(1000, 'fixed', 350, 100)).toBe(1400)
  })
  it('margin', () => {
    expect(marginMinor(2000, 1500)).toBe(500)
    expect(marginMinor(2000, null)).toBeNull()
    expect(marginPct(2000, 1500)).toBe(25)
  })
})
describe('secret field', () => {
  it('never prefilled, label shows last4', () => {
    const f = secretFromServer({ configured: true, last4: '1234' })
    expect(f.value).toBe('')
    expect(secretLabel(f, 'Configured')).toBe('Configured ••1234')
    expect(secretChange(f)).toBeUndefined()
  })
  it('replace / clear', () => {
    const f = secretFromServer({ configured: true, last4: 'abcd' })
    expect(secretChange({ ...f, mode: 'replace', value: '  new  ' })).toEqual({ value: 'new' })
    expect(secretChange({ ...f, mode: 'replace', value: ' ' })).toBeUndefined()
    expect(secretChange({ ...f, mode: 'clear' })).toEqual({ value: null })
    expect(secretChange({ ...secretFromServer(), mode: 'clear' })).toBeUndefined()
  })
})
describe('jsonDiff', () => {
  it('reports added, removed, changed', () => {
    const d = jsonDiff({ a: 1, b: { c: 2 }, x: [1] }, { a: 1, b: { c: 3 }, y: true })
    expect(d).toEqual([
      { path: 'b.c', kind: 'changed', before: 2, after: 3 },
      { path: 'x[0]', kind: 'removed', before: 1 },
      { path: 'y', kind: 'added', after: true },
    ])
    expect(jsonDiff(null, { a: 1 })).toEqual([{ path: 'a', kind: 'added', after: 1 }])
  })
  it('audit sides fall back to detail', () => {
    expect(auditSides({ at: '', actor: '', action: '', target: '', ip: null, detail: { before: { a: 1 }, after: { a: 2 } } })).toEqual({ before: { a: 1 }, after: { a: 2 } })
    expect(auditSides({ at: '', actor: '', action: '', target: '', ip: null, detail: 'x' }).after).toBe('x')
  })
})
describe('import draft', () => {
  it('rewrites selected images, drops others, appends unreferenced', () => {
    const md = 'a ![x](https://s/1.png) b ![y](https://s/2.png)'
    const map = new Map([['https://s/1.png', '/api/v1/files/AAA'], ['https://s/3.png', '/api/v1/files/CCC']])
    const sel = [{ url: 'https://s/1.png', alt: 'one [x]' }, { url: 'https://s/3.png', alt: 'three' }]
    const out = rewriteImages(md, map, sel)
    expect(out).toContain('![one  x](/api/v1/files/AAA)')
    expect(out).not.toContain('s/2.png')
    expect(out).toContain('![three](/api/v1/files/CCC)')
    expect(out).not.toContain('https://s/')
  })
  it('cleanAlt and clipboard preference', () => {
    expect(cleanAlt('a]\n[b')).toBe('a b')
    expect(pickClipboard({ getData: (t: string) => (t === 'text/html' ? '<b>x</b>' : 'x') })).toEqual({ html: '<b>x</b>' })
    expect(pickClipboard({ getData: (t: string) => (t === 'text/plain' ? 'hi' : '') })).toEqual({ text: 'hi' })
    expect(pickClipboard(null)).toEqual({})
  })
})
describe('promo form', () => {
  it('percent sent as permille, fixed as minor', () => {
    const f = { ...emptyPromoForm(), code: 'abc123', value: '12.5', min_subtotal: '50', max_discount: '20.50' }
    const r = promoToInput(f)
    expect('ok' in r && r.ok).toMatchObject({ code: 'ABC123', kind: 'percent', value: 125, min_subtotal_minor: 5000, max_discount_minor: 2050 })
    const g = promoToInput({ ...f, kind: 'fixed', value: '5' })
    expect('ok' in g && g.ok.value).toBe(500)
  })
  it('validation', () => {
    expect(promoToInput({ ...emptyPromoForm(), code: 'x' })).toEqual({ err: 'code' })
    expect(promoToInput({ ...emptyPromoForm(), code: 'ABCD', value: '100' })).toEqual({ err: 'value' })
    expect(promoToInput({ ...emptyPromoForm(), code: 'ABCD', per_user_limit: 0 })).toEqual({ err: 'limit' })
  })
  it('round trip and display', () => {
    const p = { id: '1', code: 'AB', kind: 'percent' as const, value: 125, currency: 'CNY', min_subtotal_minor: 0, max_discount_minor: null, starts_at: null, ends_at: null, usage_limit: null, per_user_limit: 1, used_count: 0, product_ids: [], category_slugs: [], enabled: true, note: null }
    expect(formFromPromo(p).value).toBe('12.5')
    expect(promoValueText(p)).toBe('12.5%')
    expect(promoValueText({ kind: 'fixed', value: 500 })).toBe('¥5.00')
  })
  it('random code uses the Crockford alphabet', () => {
    const c = randomCode(8, (n) => Uint8Array.from({ length: n }, (_, i) => i * 37))
    expect(c).toMatch(/^[0-9A-HJKMNP-TV-Z]{8}$/)
  })
})
describe('email template placeholders', () => {
  it('inserts at selection', () => {
    expect(insertPlaceholder('Hi , bye', 3, 3, '{code}')).toEqual({ text: 'Hi {code}, bye', caret: 9 })
    expect(insertPlaceholder('abc', 1, 2, '{x}').text).toBe('a{x}c')
    expect(insertPlaceholder('abc', 99, 99, '!').text).toBe('abc!')
  })
})
