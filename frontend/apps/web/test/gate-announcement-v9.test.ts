// @ts-nocheck -- node-only test helper (no @types/node in this tsconfig)
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { announcementPlan, dismissAnnouncementBanner, isAnnouncementBannerDismissed, localAnnouncementAck, rememberAnnouncementAck } from '@24goods/shared'
import { gateView } from '../src/lib/siteGate'

const src = (p: string) => readFileSync(join(__dirname, '..', p), 'utf8')

describe('site-wide visit gate (v9 §7)', () => {
  it('gates every route name declared in the router, with no allow-list', () => {
    const names = [...src('src/router.ts').matchAll(/name:\s*'([a-z-]+)'/g)].map((m) => m[1])
    expect(names.length).toBeGreaterThan(10)
    for (const name of names) {
      // the decision does not depend on the route at all
      expect(gateView({ blocked: true, midSession: false }), name).toBe('gate')
    }
  })
  it('the gate component has no route allow-list any more', () => {
    const gate = src('src/components/StorefrontGate.vue')
    expect(gate).not.toMatch(/protectedRoute/)
    expect(gate).not.toMatch(/\['home'/)
  })
  it('keeps the docked banner for a 428 raised mid-session and opens when cleared', () => {
    expect(gateView({ blocked: true, midSession: true })).toBe('banner')
    expect(gateView({ blocked: false, midSession: true })).toBe('open')
    expect(gateView({ blocked: false, midSession: false })).toBe('open')
  })
})

function memory() {
  const m = new Map<string, string>()
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), removeItem: (k: string) => void m.delete(k) }
}
const none = { dismissed: false, bannerDismissed: false, acked: 0 }

describe('announcement display and acknowledgement (v9 §8)', () => {
  it("defaults keep today's behaviour: pop-up once + the notice bar", () => {
    expect(announcementPlan({ version: 3 }, none)).toEqual({ modal: true, closable: true, banner: false, noticeBar: true })
    expect(announcementPlan({ version: 3 }, { ...none, dismissed: true }).modal).toBe(false)
  })
  it('banner and both', () => {
    expect(announcementPlan({ version: 3, display: 'banner' }, none)).toMatchObject({ modal: false, banner: true, noticeBar: false })
    expect(announcementPlan({ version: 3, display: 'both' }, none)).toMatchObject({ modal: true, banner: true })
    expect(announcementPlan({ version: 3, display: 'both' }, { ...none, bannerDismissed: true }).banner).toBe(false)
  })
  it('require_ack: no close button until acknowledged, whatever display says', () => {
    for (const display of ['modal', 'banner', 'both'] as const) {
      const p = announcementPlan({ version: 4, display, require_ack: true }, { ...none, dismissed: true })
      expect(p.modal).toBe(true)
      expect(p.closable).toBe(false)
    }
    expect(announcementPlan({ version: 4, require_ack: true }, { ...none, acked: 4 }).modal).toBe(false)
    // a new version asks again
    expect(announcementPlan({ version: 5, require_ack: true }, { ...none, acked: 4 }).modal).toBe(true)
  })
  it('stores banner dismissal and acknowledgement per version, tolerating a broken store', () => {
    const s = memory()
    expect(isAnnouncementBannerDismissed(2, s)).toBe(false)
    dismissAnnouncementBanner(2, s)
    expect(isAnnouncementBannerDismissed(2, s)).toBe(true)
    expect(isAnnouncementBannerDismissed(3, s)).toBe(false)
    rememberAnnouncementAck(7, s)
    rememberAnnouncementAck(5, s)
    expect(localAnnouncementAck(s)).toBe(7)
    const broken = {
      getItem: () => {
        throw new Error('x')
      },
      setItem: () => {
        throw new Error('x')
      },
      removeItem: () => {},
    }
    expect(() => rememberAnnouncementAck(1, broken)).not.toThrow()
    expect(localAnnouncementAck(broken)).toBe(0)
    expect(isAnnouncementBannerDismissed(1, broken)).toBe(false)
  })
})
