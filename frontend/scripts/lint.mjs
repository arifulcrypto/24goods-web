#!/usr/bin/env node
// Lightweight policy lint (ESLint skipped to keep installs small on 500 MB RAM hosts):
//  - no v-html / innerHTML (XSS) in app/shared sources
//  - no runtime third-party CDNs / Google fonts / analytics / recaptcha
//  - no console.log left in sources
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, dirname, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const dirs = ['apps/web/src', 'apps/web/index.html', 'apps/admin/src', 'apps/admin/index.html', 'packages/shared/src']
const rules = [
  { re: /\bv-html\b/, msg: 'v-html is forbidden (XSS); render text with {{ }}' },
  { re: /\.innerHTML\s*=|insertAdjacentHTML|document\.write\(/, msg: 'raw HTML injection is forbidden' },
  { re: /fonts\.(googleapis|gstatic)\.com|googletagmanager|google-analytics|recaptcha|gstatic\.com/i, msg: 'Google assets are forbidden' },
  { re: /(src|href)\s*=\s*["']https?:\/\/(?!www\.w3\.org)/i, msg: 'no runtime third-party asset URLs (self-host everything)' },
  { re: /cdn\.jsdelivr|unpkg\.com|cdnjs|bootcdn|staticfile\.org|yzcdn/i, msg: 'third-party CDN is forbidden' },
  { re: /\bconsole\.log\(/, msg: 'remove console.log' },
  { re: /\beval\(|new Function\(/, msg: 'eval/new Function is forbidden' },
]
const exts = /\.(vue|ts|html|css|mjs)$/
let problems = 0
function check(path) {
  const st = statSync(path)
  if (st.isDirectory()) {
    for (const f of readdirSync(path)) if (f !== 'node_modules' && f !== 'dist') check(join(path, f))
    return
  }
  if (!exts.test(path) || path.endsWith('.d.ts')) return
  const lines = readFileSync(path, 'utf8').split('\n')
  lines.forEach((line, i) => {
    for (const r of rules) {
      if (r.re.test(line)) {
        problems++
        console.log(`${relative(root, path)}:${i + 1}  ${r.msg}\n    ${line.trim()}`)
      }
    }
  })
}
for (const d of dirs) {
  try {
    check(join(root, d))
  } catch (e) {
    if (e.code !== 'ENOENT') throw e
  }
}
if (problems) {
  console.error(`\nlint: ${problems} problem(s)`)
  process.exit(1)
}
console.log('lint: ok')
