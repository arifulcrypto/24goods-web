// AGENTS.md §1.8: no runtime third-party CDN assets. Vant's icon CSS ships the font inline (woff2)
// with an `//at.alicdn.com/...woff` fallback; strip that fallback at build time, then fail the build
// if any emitted CSS still points at a remote url() or anything mentions alicdn (issue 48 §1.1).
// Source lint (scripts/lint.mjs) never sees dependency CSS, so the bundle is checked here.

const VANT_FALLBACK = /,\s*url\(\s*["']?(?:https?:)?\/\/at\.alicdn\.com\/[^)]*\)\s*format\(\s*["']woff["']\s*\)/g
const REMOTE_URL = /url\(\s*["']?(?:https?:)?\/\//i

/** @returns {import('vite').Plugin} */
export function noRemoteAssets() {
  return {
    name: 'kesiv-no-remote-assets',
    enforce: 'pre',
    transform(code, id) {
      if (!/\.css(?:$|\?)/.test(id) || !code.includes('alicdn')) return null
      return { code: code.replace(VANT_FALLBACK, ''), map: null }
    },
    generateBundle(_opts, bundle) {
      for (const out of Object.values(bundle)) {
        const text = out.type === 'asset' ? (typeof out.source === 'string' ? out.source : '') : out.code
        if (text.includes('alicdn.com')) this.error(`${out.fileName}: references alicdn.com (third-party CDN)`)
        if (out.fileName.endsWith('.css') && REMOTE_URL.test(text)) this.error(`${out.fileName}: CSS loads a remote url()`)
      }
    },
  }
}
