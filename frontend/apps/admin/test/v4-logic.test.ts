import { describe, expect, it } from 'vitest'
import { insertAt, prefixLines, renderMarkdownPreview, safeUrl, wrapSelection } from '../src/lib/markdown'
import { cleanQuickQuestions, percentToPermille, permilleToPercent, promptChips, validateBuyerInput } from '../src/lib/buyerInput'
import { BUYER_INPUT_DEFAULT } from '../src/api-v4'

describe('markdown preview', () => {
  it('escapes html and blocks unsafe urls', () => {
    const h = renderMarkdownPreview('<script>x</script> [a](javascript:alert(1)) ![](data:text/html,x)')
    expect(h).not.toContain('<script')
    expect(h).not.toContain('href="javascript')
    expect(h).not.toContain('<img')
  })
  it('renders headings, lists, bold, uploaded images', () => {
    const h = renderMarkdownPreview('## T\n\n- a\n- **b**\n\n![](/api/v1/files/abc-123)')
    expect(h).toContain('<h2>T</h2>')
    expect(h).toContain('<li><strong>b</strong></li>')
    expect(h).toContain('<img src="/api/v1/files/abc-123"')
  })
  it('safeUrl', () => {
    expect(safeUrl('https://a.b/c')).toBe('https://a.b/c')
    expect(safeUrl('//evil.com')).toBeNull()
    expect(safeUrl('/api/v1/files/../x')).toBeNull()
  })
  it('toolbar edits', () => {
    expect(wrapSelection('hello', 0, 5, '**', '**', 'x').text).toBe('**hello**')
    expect(prefixLines('a\nb', 0, 3, '- ').text).toBe('- a\n- b')
    expect(insertAt('ab', 1, 'X').text).toBe('aXb')
  })
})

describe('buyer input + site helpers', () => {
  it('chips split backtick segments', () => {
    expect(promptChips('open `https://x.y` now')).toEqual([
      { kind: 'text', value: 'open ' },
      { kind: 'code', value: 'https://x.y' },
      { kind: 'text', value: ' now' },
    ])
  })
  it('validates max_len', () => {
    expect(validateBuyerInput({ ...BUYER_INPUT_DEFAULT, max_len: 0 })).toBe('max_len')
    expect(validateBuyerInput({ ...BUYER_INPUT_DEFAULT, max_len: 4001 })).toBe('max_len')
    expect(validateBuyerInput(BUYER_INPUT_DEFAULT)).toBeNull()
  })
  it('permille <-> percent with 0.1 step', () => {
    expect(permilleToPercent(30)).toBe(3)
    expect(percentToPermille(2.7)).toBe(27)
    expect(percentToPermille(0.1 + 0.2)).toBe(3)
    expect(cleanQuickQuestions([' a ', '', 'b'])).toEqual(['a', 'b'])
  })
})
