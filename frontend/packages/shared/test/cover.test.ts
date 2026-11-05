import { describe, expect, it } from 'vitest'
import { coverColors, coverInitials } from '../src/ui/cover'

describe('generated cover', () => {
  it('uses one CJK char and skips bracket tags', () => {
    expect(coverInitials('【优质】全新个人谷歌Gmail邮箱')).toBe('全')
  })
  it('uses two Latin initials', () => {
    expect(coverInitials('Steam Wallet 50 USD')).toBe('SW')
    expect(coverInitials('[HOT] netflix premium')).toBe('NP')
  })
  it('never returns empty', () => {
    expect(coverInitials('   ')).toBe('?')
    expect(coverInitials('!!!')).toBe('!')
  })
  it('is deterministic', () => {
    expect(coverColors('a')).toEqual(coverColors('a'))
  })
})
