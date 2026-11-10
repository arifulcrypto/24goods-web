import { writeFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { AVATAR_PRESETS, avatarPresetUrl, defaultAvatarPreset, isAvatarPreset, resolveAvatar } from '../src/avatars'

describe('generic avatars (v11)', () => {
  it('renders every preset as a distinct inline SVG (no network asset)', () => {
    const urls = AVATAR_PRESETS.map(avatarPresetUrl)
    expect(new Set(urls).size).toBe(AVATAR_PRESETS.length)
    for (const u of urls) {
      expect(u.startsWith('data:image/svg+xml;utf8,')).toBe(true)
      const svg = decodeURIComponent(u.slice('data:image/svg+xml;utf8,'.length))
      expect(svg).toMatch(/^<svg xmlns="http:\/\/www\.w3\.org\/2000\/svg" viewBox="0 0 64 64">/)
      expect(svg).not.toMatch(/https?:\/\/(?!www\.w3\.org)/) // nothing external
      expect(svg).not.toMatch(/<script|on[a-z]+=/i)
    }
    if (process.env.AVATAR_PREVIEW) {
      const imgs = urls.map((u) => `<img src="${u}" width="96" height="96" style="margin:6px">`).join('')
      writeFileSync(process.env.AVATAR_PREVIEW, `<!doctype html><body style="margin:0;background:#fff;width:440px">${imgs}</body>`)
    }
  })

  it('gives a stable default per seed and validates ids', () => {
    expect(defaultAvatarPreset('user-1')).toBe(defaultAvatarPreset('user-1'))
    const spread = new Set(Array.from({ length: 200 }, (_, i) => defaultAvatarPreset(`u${i}`)))
    expect(spread.size).toBeGreaterThan(10)
    expect(isAvatarPreset('p07')).toBe(true)
    expect(isAvatarPreset('p99')).toBe(false)
    expect(avatarPresetUrl('nope')).toBe(avatarPresetUrl('p01'))
  })

  it('prefers an upload, then the chosen preset, then the seeded default', () => {
    expect(resolveAvatar({ url: '/api/v1/avatars/x', preset: 'p02', seed: 's' })).toBe('/api/v1/avatars/x')
    expect(resolveAvatar({ preset: 'p02', seed: 's' })).toBe(avatarPresetUrl('p02'))
    expect(resolveAvatar({ seed: 's' })).toBe(avatarPresetUrl(defaultAvatarPreset('s')))
  })
})
