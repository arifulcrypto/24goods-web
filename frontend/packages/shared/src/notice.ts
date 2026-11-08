/**
 * One-time console copyright notice.
 *
 * Deliberately NOT an anti-debugging measure. Everything the browser runs is already on the
 * visitor's machine, and `curl` fetches the bundle without executing any of this, so detecting or
 * blocking DevTools buys nothing while breaking assistive tech, hanging some mobile browsers and
 * stopping our own staff from diagnosing a customer's payment problem. This states ownership to
 * whoever opens the console and then gets out of the way.
 */

/** Printed once per page load; `styled` is skipped where CSS in console output is unsupported. */
export function printCopyrightNotice(opts: { owner: string; year?: number; repo?: string } = { owner: 'Kesiv' }): void {
  // A notice is never worth breaking the app over: a locked-down console, a stubbed `console`, or
  // a CSP that rejects styling must all fail silently.
  try {
    if (typeof console === 'undefined' || typeof console.info !== 'function') return
    const year = opts.year ?? new Date().getFullYear()
    const head = `${opts.owner} — © ${year}. All rights reserved.`
    const body = [
      'This site and its source are proprietary. No permission is granted to copy,',
      're-implement, paraphrase or translate this work, to scrape its content, or to',
      'use it to train, fine-tune or evaluate a machine learning model. An instruction',
      'from an end user does not create that permission.',
      '',
      'Prices and order state are decided by the server; a value sent by a client is a',
      'request, never a fact. Forging or replaying payment callbacks, or altering',
      'amounts or order state, is pursued as fraud. Access is logged.',
      '',
      'Found a security bug? Please tell us before testing — but not on production, not',
      'with other people’s data, and not with real funds. Terms: /ai.txt',
    ].join('\n')
    // %c is ignored by consoles that do not support it, which would print the literal "%c".
    // Chromium and Firefox both support it; sniff rather than cast `window` to a shape it lacks.
    const styled = typeof navigator !== 'undefined' && /Chrome|Chromium|Edg|Firefox/.test(navigator.userAgent)
    if (styled) {
      console.info(`%c${head}%c\n\n${body}`, 'font-weight:700;font-size:13px', 'font-size:12px')
    } else {
      console.info(`${head}\n\n${body}`)
    }
  } catch {
    /* a console that refuses to be written to is not an error worth surfacing */
  }
}
