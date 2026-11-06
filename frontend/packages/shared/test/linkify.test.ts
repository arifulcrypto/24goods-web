import { describe, expect, it } from 'vitest'
import { linkify } from '../src/chat/linkify'

const kinds = (s: string, o?: { code?: boolean }) => linkify(s, o).map((x) => `${x.kind}:${x.text}`)

describe('linkify', () => {
  it('plain text stays one segment', () => {
    expect(kinds('hello world')).toEqual(['text:hello world'])
    expect(linkify('')).toEqual([])
    expect(linkify(null)).toEqual([])
  })
  it('detects urls and trims trailing punctuation', () => {
    expect(kinds('see https://a.com/x?y=1, ok')).toEqual(['text:see ', 'url:https://a.com/x?y=1', 'text:, ok'])
    expect(kinds('(https://a.com/x)')).toEqual(['text:(', 'url:https://a.com/x', 'text:)'])
    expect(kinds('https://a.com/x。后面')).toEqual(['url:https://a.com/x', 'text:。后面'])
    expect(kinds('「https://a.com」')).toEqual(['text:「', 'url:https://a.com', 'text:」'])
    const u = linkify('http://Example.com/p')[0]!
    expect(u.host).toBe('example.com')
    expect(u.href).toBe('http://example.com/p')
  })
  it('only http(s)', () => {
    expect(kinds('javascript:alert(1)')).toEqual(['text:javascript:alert(1)'])
    expect(kinds('ftp://x.com/a')).toEqual(['text:ftp://x.com/a'])
    expect(kinds('https://')).toEqual(['text:https://'])
    expect(linkify('https://u:p@evil.com/').some((s) => s.kind === 'url')).toBe(false)
  })
  it('emails', () => {
    expect(kinds('mail a.b+c@mail.example.com now')).toEqual(['text:mail ', 'email:a.b+c@mail.example.com', 'text: now'])
    expect(kinds('a@b.')).toEqual(['text:a@b.'])
    expect(kinds('x@y.com.')).toEqual(['email:x@y.com', 'text:.'])
  })
  it('order numbers', () => {
    expect(kinds('order GABCDEFGH2345 ok')).toEqual(['text:order ', 'order_no:GABCDEFGH2345', 'text: ok'])
    expect(kinds('GABCDEFGH23456')).toEqual(['text:GABCDEFGH23456'])
    expect(kinds('gabcdefgh2345')).toEqual(['text:gabcdefgh2345'])
    expect(kinds('订单GABCDEFGH2345。')).toEqual(['text:订单', 'order_no:GABCDEFGH2345', 'text:。'])
  })
  it('numbers: 6+ digits, optional single separators, max 32 chars', () => {
    expect(kinds('code 123456 end')).toEqual(['text:code ', 'number:123456', 'text: end'])
    expect(kinds('12345')).toEqual(['text:12345'])
    expect(kinds('call 138 0013 8000')).toEqual(['text:call ', 'number:138 0013 8000'])
    expect(kinds('622-2020-1234')).toEqual(['number:622-2020-1234'])
    expect(kinds('1  2  3  4  5  6')).toEqual(['text:1  2  3  4  5  6'])
    expect(kinds('1'.repeat(40))).toEqual(['text:' + '1'.repeat(40)])
    expect(kinds('1'.repeat(32))).toEqual(['number:' + '1'.repeat(32)])
  })
  it('code spans only with the code option', () => {
    expect(kinds('open `https://chatgpt.com/api/auth/session` now')).toEqual([
      'text:open `',
      'url:https://chatgpt.com/api/auth/session',
      'text:` now',
    ])
    expect(kinds('open `https://chatgpt.com/api/auth/session` now', { code: true })).toEqual([
      'text:open ',
      'code:https://chatgpt.com/api/auth/session',
      'text: now',
    ])
    expect(kinds('a `` b', { code: true })).toEqual(['text:a `` b'])
  })
  it('url and number inside code are not re-tokenised', () => {
    expect(kinds('`123456`', { code: true })).toEqual(['code:123456'])
  })
  it('preserves the input exactly', () => {
    const s = 'a https://x.io/a.b?c=d, mail me@x.io GABCDEFGH2345 1234 5678 `z` !'
    expect(linkify(s, { code: true }).map((x) => (x.kind === 'code' ? '`' + x.text + '`' : x.text)).join('')).toBe(s)
  })
  it('is linear on hostile input', () => {
    const t0 = Date.now()
    linkify('a'.repeat(100_000) + '@' + 'b'.repeat(100_000))
    linkify('1 '.repeat(50_000))
    linkify('http://' + 'a.'.repeat(50_000))
    linkify('`'.repeat(50_000), { code: true })
    expect(Date.now() - t0).toBeLessThan(2000)
  })
})
