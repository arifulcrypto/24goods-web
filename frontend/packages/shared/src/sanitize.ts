const DROP = new Set(['script', 'style', 'iframe', 'frame', 'object', 'embed', 'form', 'input', 'button', 'textarea', 'select', 'link', 'meta', 'base', 'svg', 'math', 'template'])
const SAFE_PROTO = /^(https?:|mailto:|#|\/(?!\/))/i

/** Defence-in-depth sanitiser for already-server-sanitised HTML. Returns '' without a DOM. */
export function sanitizeHtml(html: string): string {
  if (!html || typeof DOMParser === 'undefined') return ''
  const doc = new DOMParser().parseFromString(`<body>${html}`, 'text/html')
  const walk = (node: Element) => {
    for (const child of Array.from(node.children)) {
      if (DROP.has(child.tagName.toLowerCase())) {
        child.remove()
        continue
      }
      for (const attr of Array.from(child.attributes)) {
        const name = attr.name.toLowerCase()
        const val = attr.value.trim()
        if (name.startsWith('on') || name === 'style' || name === 'srcdoc' || name === 'formaction') child.removeAttribute(attr.name)
        else if ((name === 'href' || name === 'src' || name === 'xlink:href') && !SAFE_PROTO.test(val.replace(/[\u0000- ]/g, ''))) child.removeAttribute(attr.name)
      }
      if (child.tagName === 'A') {
        child.setAttribute('rel', 'noopener noreferrer nofollow')
        if (child.getAttribute('href')?.startsWith('http')) child.setAttribute('target', '_blank')
      }
      walk(child)
    }
  }
  walk(doc.body)
  return doc.body.innerHTML
}
