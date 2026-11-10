/**
 * v11 generic character avatars: 16 original, neutral figures built from simple shapes (varied skin
 * tones, hair styles and colours). Rendered as inline SVG data URIs — no files, no CDN, works in the
 * buyer app, the console and the chat bubbles alike. A preset id is stored server-side
 * (`users.avatar_preset`); a user with no upload and no preset gets a stable preset from their id.
 */

export const AVATAR_PRESETS = [
  'p01', 'p02', 'p03', 'p04', 'p05', 'p06', 'p07', 'p08',
  'p09', 'p10', 'p11', 'p12', 'p13', 'p14', 'p15', 'p16',
] as const
export type AvatarPreset = (typeof AVATAR_PRESETS)[number]

type Hair = 'short' | 'side' | 'long' | 'bun' | 'curly' | 'buzz' | 'bob' | 'cap'

interface Spec {
  bg: string
  skin: string
  hair: string
  style: Hair
  shirt: string
  glasses?: boolean
}

// Background and shirt colours keep contrast with every skin tone in light and dark themes.
const SPECS: Record<AvatarPreset, Spec> = {
  p01: { bg: '#DCEBFF', skin: '#F2C7A5', hair: '#3B2A20', style: 'short', shirt: '#2F6FDE' },
  p02: { bg: '#FFE3D6', skin: '#E0A57E', hair: '#1F1A17', style: 'long', shirt: '#D9534F' },
  p03: { bg: '#DDF4E4', skin: '#8D5A3B', hair: '#151110', style: 'curly', shirt: '#2E9E5B' },
  p04: { bg: '#EFE3FF', skin: '#F7D7BF', hair: '#C98B3A', style: 'bob', shirt: '#7B4FD6' },
  p05: { bg: '#FFF2CC', skin: '#C68863', hair: '#2A1F1A', style: 'side', shirt: '#E08A1E', glasses: true },
  p06: { bg: '#D9F2F7', skin: '#5C3A28', hair: '#0F0C0B', style: 'buzz', shirt: '#1C8DA6' },
  p07: { bg: '#FCE1EC', skin: '#F1C2A0', hair: '#7A3E22', style: 'bun', shirt: '#C2457A' },
  p08: { bg: '#E6E9EF', skin: '#D9A07A', hair: '#4A4A4A', style: 'cap', shirt: '#44546A' },
  p09: { bg: '#E1F0FF', skin: '#A86B47', hair: '#1B1412', style: 'long', shirt: '#3A7BD5', glasses: true },
  p10: { bg: '#E8F7DD', skin: '#F4CFB2', hair: '#E2B659', style: 'short', shirt: '#5C9E31' },
  p11: { bg: '#FFE6D1', skin: '#7A4B32', hair: '#120E0C', style: 'bun', shirt: '#D2691E' },
  p12: { bg: '#EAE4FF', skin: '#E6B391', hair: '#2E2420', style: 'curly', shirt: '#5B5BD6' },
  p13: { bg: '#DFF5F0', skin: '#F0C4A2', hair: '#9A9A9A', style: 'side', shirt: '#2A9D8F' },
  p14: { bg: '#FDE2E2', skin: '#B5764F', hair: '#3A2418', style: 'bob', shirt: '#B23A48', glasses: true },
  p15: { bg: '#E3ECF7', skin: '#F6D2B8', hair: '#151515', style: 'buzz', shirt: '#30475E' },
  p16: { bg: '#FFF0D9', skin: '#8F5C3E', hair: '#5A2E1A', style: 'cap', shirt: '#C08A2B' },
}

function hairBack(s: Spec): string {
  switch (s.style) {
    case 'long':
      return `<path d="M17 30c0-10 6-17 15-17s15 7 15 17v16H17z" fill="${s.hair}"/>`
    case 'bob':
      return `<path d="M18 31c0-10 6-16 14-16s14 6 14 16v8c0 2-2 3-4 3H22c-2 0-4-1-4-3z" fill="${s.hair}"/>`
    case 'bun':
      return `<circle cx="32" cy="12" r="6" fill="${s.hair}"/>`
    case 'curly':
      return ['22,20', '27,15', '32,13', '37,15', '42,20', '45,26', '19,26']
        .map((p) => {
          const [x, y] = p.split(',')
          return `<circle cx="${x}" cy="${y}" r="6" fill="${s.hair}"/>`
        })
        .join('')
    default:
      return ''
  }
}

function hairFront(s: Spec): string {
  switch (s.style) {
    case 'short':
      return `<path d="M20 27c0-8 5-12 12-12s12 4 12 12c-3-3-7-5-12-5s-9 2-12 5z" fill="${s.hair}"/>`
    case 'side':
      return `<path d="M20 28c0-9 5-13 12-13 8 0 12 5 12 11-6 0-13-2-18-6-1 3-3 6-6 8z" fill="${s.hair}"/>`
    case 'long':
    case 'bob':
      return `<path d="M20 28c0-8 5-12 12-12s12 4 12 12c-4-2-8-6-10-9-3 4-8 8-14 9z" fill="${s.hair}"/>`
    case 'bun':
      return `<path d="M20 27c0-8 5-12 12-12s12 4 12 12c-3-3-7-4-12-4s-9 1-12 4z" fill="${s.hair}"/>`
    case 'curly':
      return ''
    case 'buzz':
      return `<path d="M20.5 26c0-7 5-11 11.5-11s11.5 4 11.5 11c-3-2-7-3-11.5-3s-8.5 1-11.5 3z" fill="${s.hair}" opacity=".85"/>`
    case 'cap':
      return `<path d="M19 25c0-7 6-11 13-11s13 4 13 11z" fill="${s.shirt}"/><rect x="30" y="23" width="19" height="3.5" rx="1.75" fill="${s.shirt}"/>`
  }
}

function svg(s: Spec): string {
  const glasses = s.glasses
    ? '<g fill="none" stroke="#1E1E1E" stroke-width="1.4"><circle cx="27.5" cy="31" r="3.6"/><circle cx="36.5" cy="31" r="3.6"/><path d="M31.1 31h1.8"/></g>'
    : ''
  return (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">' +
    `<rect width="64" height="64" rx="32" fill="${s.bg}"/>` +
    hairBack(s) +
    `<path d="M12 64c1-12 9-18 20-18s19 6 20 18z" fill="${s.shirt}"/>` +
    `<rect x="28" y="38" width="8" height="9" rx="3" fill="${s.skin}"/>` +
    `<ellipse cx="32" cy="29" rx="11" ry="12" fill="${s.skin}"/>` +
    hairFront(s) +
    '<circle cx="27.5" cy="31" r="1.3" fill="#1E1E1E"/><circle cx="36.5" cy="31" r="1.3" fill="#1E1E1E"/>' +
    '<path d="M28.5 36c2 1.6 5 1.6 7 0" fill="none" stroke="#1E1E1E" stroke-width="1.3" stroke-linecap="round"/>' +
    glasses +
    '</svg>'
  )
}

const cache = new Map<string, string>()

export function isAvatarPreset(v: unknown): v is AvatarPreset {
  return typeof v === 'string' && (AVATAR_PRESETS as readonly string[]).includes(v)
}

/** Data URI of a preset (unknown ids fall back to the first preset). */
export function avatarPresetUrl(preset: string): string {
  const id: AvatarPreset = isAvatarPreset(preset) ? preset : 'p01'
  let url = cache.get(id)
  if (!url) {
    url = 'data:image/svg+xml;utf8,' + encodeURIComponent(svg(SPECS[id]))
    cache.set(id, url)
  }
  return url
}

/** Stable preset for a user/seed (FNV-1a over the id), so everyone without a picture still has a face. */
export function defaultAvatarPreset(seed: string | null | undefined): AvatarPreset {
  let h = 0x811c9dc5
  for (const ch of seed ?? '') {
    h ^= ch.codePointAt(0) ?? 0
    h = Math.imul(h, 0x01000193) >>> 0
  }
  return AVATAR_PRESETS[h % AVATAR_PRESETS.length]
}

/** Avatar to show: uploaded picture, else chosen preset, else the stable default for the seed. */
export function resolveAvatar(opts: { url?: string | null; preset?: string | null; seed?: string | null }): string {
  if (opts.url) return opts.url
  if (opts.preset && isAvatarPreset(opts.preset)) return avatarPresetUrl(opts.preset)
  return avatarPresetUrl(defaultAvatarPreset(opts.seed))
}
