#!/usr/bin/env node
// Sums gzip sizes of the web app's entry chunk + all of its *static* imports (what the browser
// must download before first render) using Vite's build manifest. Fails above the budget.
import { readFileSync, existsSync } from 'node:fs'
import { gzipSync } from 'node:zlib'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const app = process.argv[2] ?? 'web'
const budgetKb = Number(process.argv[3] ?? (app === 'web' ? 150 : 0))
const dist = join(root, 'apps', app, 'dist')
const manifestPath = join(dist, '.vite', 'manifest.json')
if (!existsSync(manifestPath)) {
  console.error(`[size] ${manifestPath} not found — run the ${app} build first`)
  process.exit(1)
}
const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
const entryKey = Object.keys(manifest).find((k) => manifest[k].isEntry)
if (!entryKey) {
  console.error('[size] no entry chunk in manifest')
  process.exit(1)
}

const seen = new Set()
const css = new Set()
;(function walk(key) {
  if (seen.has(key)) return
  seen.add(key)
  const chunk = manifest[key]
  for (const c of chunk.css ?? []) css.add(c)
  for (const imp of chunk.imports ?? []) walk(imp)
})(entryKey)

const gz = (file) => gzipSync(readFileSync(join(dist, file)), { level: 9 }).length
let jsTotal = 0
const rows = []
for (const key of seen) {
  const file = manifest[key].file
  const size = gz(file)
  jsTotal += size
  rows.push([file, size])
}
let cssTotal = 0
for (const f of css) cssTotal += gz(f)

rows.sort((a, b) => b[1] - a[1])
const kb = (n) => (n / 1024).toFixed(1) + ' KB'
console.log(`[size] ${app}: entry ${manifest[entryKey].file} + ${seen.size - 1} static import(s)`)
for (const [f, s] of rows) console.log(`  ${kb(s).padStart(9)}  ${f}`)
console.log(`[size] initial JS (gzip): ${kb(jsTotal)}${budgetKb ? ` / budget ${budgetKb} KB` : ''}`)
console.log(`[size] initial CSS (gzip): ${kb(cssTotal)}`)
if (budgetKb && jsTotal > budgetKb * 1024) {
  console.error(`[size] FAIL: initial JS exceeds ${budgetKb} KB gzip`)
  process.exit(1)
}
