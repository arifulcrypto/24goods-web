import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { flattenMessages, type MessageTree } from '@24goods/shared'
import en from '../src/i18n/en'

function walk(d: string): string[] {
  return readdirSync(d).flatMap((f) => {
    const p = join(d, f)
    return statSync(p).isDirectory() ? walk(p) : /\.(vue|ts)$/.test(f) && !/i18n/.test(p) ? [p] : []
  })
}
describe('v5 i18n keys', () => {
  it('every statically referenced v5.* key exists in en', () => {
    const have = new Set(Object.keys(flattenMessages(en as unknown as MessageTree)))
    const missing: string[] = []
    for (const f of walk(join(__dirname, '../src'))) {
      const src = readFileSync(f, 'utf8')
      for (const m of src.matchAll(/['"`](v5\.[A-Za-z0-9_.]+)['"`]/g)) if (!have.has(m[1]!)) missing.push(`${f.split('/src/')[1]}: ${m[1]}`)
    }
    expect(missing).toEqual([])
  })
})
