// @ts-nocheck -- node-only test helper (no @types/node in this tsconfig)
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { flattenMessages } from '@24goods/shared'
import { messages } from '../src/i18n'

function walk(dir: string, out: string[] = []): string[] {
  for (const f of readdirSync(dir)) {
    const p = join(dir, f)
    if (statSync(p).isDirectory()) walk(p, out)
    else if (/\.(vue|ts)$/.test(f)) out.push(p)
  }
  return out
}

describe('i18n key usage', () => {
  it('every static t()/te() key used by the web app and shared UI exists in en and zh-CN', () => {
    const files = [...walk(join(__dirname, '../src')), ...walk(join(__dirname, '../../../packages/shared/src'))]
    const en = flattenMessages(messages.en)
    const zh = flattenMessages(messages['zh-CN'])
    const missing: string[] = []
    for (const f of files) {
      const src = readFileSync(f, 'utf8')
      for (const m of src.matchAll(/\bt[e]?\(\s*'([a-zA-Z0-9_]+(?:\.[a-zA-Z0-9_]+)+)'/g)) {
        const k = m[1]!
        if (!(k in en) || !(k in zh)) missing.push(`${f.split('/src/')[1]}: ${k}`)
      }
    }
    expect([...new Set(missing)]).toEqual([])
  })
})
