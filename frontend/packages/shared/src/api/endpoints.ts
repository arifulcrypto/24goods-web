import type { ApiClient } from './client'
import { seg } from './client'
import type {
  AfterSales,
  AfterSalesInput,
  Article,
  ArticleSummary,
  InstantResult,
  LookupOrder,
  PowChallenge,
  PowScope,
  PowSolution,
  SiteInfo,
  AdminOrder,
  AdminOrderDetail,
  AdminProduct,
  AdminProductInput,
  AdminStats,
  AgentInfo,
  AgentStatus,
  AgentStatusValue,
  Attachment,
  AuditRow,
  CardImportResult,
  CardRow,
  CardStatus,
  Category,
  Conversation,
  ConversationFilter,
  Locale,
  Message,
  MessageQuery,
  Order,
  OrderStatus,
  Paginated,
  Product,
  ProductStatus,
  ProductSummary,
  QuoteCardPayload,
  QuoteInput,
  SendMessageBody,
  User,
  LedgerRow,
} from '../types'

export const DEFAULT_PAY_CHANNEL = 'zpay_alipay'

export interface UploadOptions {
  kind: 'image' | 'voice' | 'file'
  /** voice only, ≤ 60000 */
  durationMs?: number
  filename?: string
}

export function uploadForm(file: Blob, o: UploadOptions): FormData {
  const fd = new FormData()
  const name = o.filename ?? (o.kind === 'voice' ? 'voice' : o.kind === 'file' ? 'message.txt' : 'image')
  fd.append('file', file, name)
  fd.append('kind', o.kind)
  if (o.kind === 'voice' && o.durationMs !== undefined) {
    fd.append('duration_ms', String(Math.min(60000, Math.max(0, Math.round(o.durationMs)))))
  }
  return fd
}

/** Buyer / public endpoints. */
export function createBuyerApi(c: ApiClient) {
  return {
    auth: {
      register: (b: {
        email: string
        password: string
        display_name: string
        locale: Locale
        pow?: PowSolution
      }) => c.post<User>('/auth/register', b, { skipAuthHandler: true }),
      /** `email` also accepts an instant-account login handle (`u-xxxxxxx`). */
      login: (b: { email: string; password: string; pow?: PowSolution }) =>
        c.post<User>('/auth/login', b, { skipAuthHandler: true }),
      logout: () => c.post<void>('/auth/logout', undefined, { skipAuthHandler: true }),
      me: () => c.get<User>('/auth/me', undefined, { skipAuthHandler: true }),
      /** v3: one-tap instant account; the credentials are returned once. */
      instant: (b: { pow: PowSolution; email?: string }) =>
        c.post<InstantResult>('/auth/instant', b, { skipAuthHandler: true }),
    },
    /** v3: brand, announcement, defaults, fixed pages. */
    site: () => c.get<SiteInfo>('/site'),
    pow: {
      challenge: (scope: PowScope) => c.get<PowChallenge>('/pow/challenge', { scope }),
    },
    me: {
      update: (b: { display_name?: string; locale?: Locale }) => c.patch<User>('/me', b),
      credentials: (b: { current_password?: string; email?: string; new_password?: string }) =>
        c.post<User>('/me/credentials', b),
      remove: (b: { password?: string; confirm: 'DELETE' }) => c.post<void>('/me/delete', b),
    },
    categories: () => c.get<Category[]>('/categories'),
    products: (q: { q?: string; category?: string; page?: number; page_size?: number }) =>
      c.get<Paginated<ProductSummary>>('/products', q),
    /** v3: summaries for ≤20 ids, in the given order (recently viewed). */
    productsByIds: (ids: readonly string[]) =>
      c.get<{ items: ProductSummary[] }>('/products', { ids: ids.slice(0, 20).join(',') }),
    recommended: (q: { for?: string; recent?: readonly string[]; limit?: number }) =>
      c.get<{ items: ProductSummary[] }>('/products/recommended', {
        for: q.for,
        recent: q.recent?.length ? q.recent.slice(0, 20).join(',') : undefined,
        limit: q.limit ?? 8,
      }),
    product: (id: string) => c.get<Product>(`/products/${seg(id)}`),
    articles: {
      list: (q: { category?: string; page?: number; page_size?: number } = {}) =>
        c.get<Paginated<ArticleSummary>>('/articles', q),
      get: (slug: string) => c.get<Article>(`/articles/${seg(slug)}`),
    },
    lookup: {
      find: (b: { order_no: string; access_code: string; pow: PowSolution }) =>
        c.post<LookupOrder>('/lookup', b, { skipAuthHandler: true }),
      reveal: (b: { order_no: string; access_code: string }) =>
        c.post<{ cards: { id: string; code: string }[] }>('/lookup/reveal', b, { skipAuthHandler: true }),
    },
    supportStatus: () => c.get<AgentStatus>('/support/status'),
    conversations: {
      open: (product_id?: string) =>
        c.post<Conversation>('/conversations', product_id ? { product_id } : {}),
      list: () => c.get<Conversation[]>('/conversations'),
      messages: (id: string, q: MessageQuery = {}) =>
        c.get<Message[]>(`/conversations/${seg(id)}/messages`, { ...q }),
      send: (id: string, b: SendMessageBody) =>
        c.post<Message>(`/conversations/${seg(id)}/messages`, b),
      read: (id: string, message_id: number) =>
        c.post<void>(`/conversations/${seg(id)}/read`, { message_id }),
    },
    upload: (file: Blob, o: UploadOptions) => c.upload<Attachment>('/uploads', uploadForm(file, o)),
    quotes: {
      accept: (id: string) => c.post<Order>(`/quotes/${seg(id)}/accept`),
    },
    orders: {
      /** `src` = traffic attribution tag from a direct buy link (`[a-z0-9_-]{0,32}`). */
      create: (product_id: string, qty: number, src?: string | null) =>
        c.post<Order>('/orders', src ? { product_id, qty, src } : { product_id, qty }),
      list: (q: { page?: number; page_size?: number } = {}) => c.get<Paginated<Order>>('/orders', q),
      get: (id: string) => c.get<Order>(`/orders/${seg(id)}`),
      pay: (id: string, channel: string = DEFAULT_PAY_CHANNEL) =>
        c.post<{ pay_url: string }>(`/orders/${seg(id)}/pay`, { channel }),
      reveal: (id: string) => c.post<{ secrets: string[] }>(`/orders/${seg(id)}/reveal`),
      confirm: (id: string) => c.post<Order>(`/orders/${seg(id)}/confirm`),
      afterSales: (id: string, b: AfterSalesInput) => c.post<AfterSales>(`/orders/${seg(id)}/after-sales`, b),
    },
    streamUrl: () => c.url('/stream'),
  }
}
export type BuyerApi = ReturnType<typeof createBuyerApi>

/** Staff endpoints under /admin (role agent or admin). */
export function createAdminApi(c: ApiClient) {
  const buyer = createBuyerApi(c)
  return {
    auth: buyer.auth,
    categories: buyer.categories,
    upload: buyer.upload,
    stats: () => c.get<AdminStats>('/admin/stats'),
    products: {
      list: (q: { q?: string; status?: ProductStatus | ''; page?: number; page_size?: number }) =>
        c.get<Paginated<AdminProduct>>('/admin/products', q),
      create: (b: AdminProductInput) => c.post<AdminProduct>('/admin/products', b),
      update: (id: string, b: Partial<AdminProductInput>) =>
        c.patch<AdminProduct>(`/admin/products/${seg(id)}`, b),
      remove: (id: string) => c.delete<void>(`/admin/products/${seg(id)}`),
      uploadImage: (id: string, file: Blob, filename = 'image') => {
        const fd = new FormData()
        fd.append('file', file, filename)
        return c.upload<{ url: string }>(`/admin/products/${seg(id)}/images`, fd)
      },
      importCards: (id: string, lines: string) =>
        c.post<CardImportResult>(`/admin/products/${seg(id)}/cards/import`, { lines }),
      cards: (id: string, q: { status?: CardStatus | ''; page?: number; page_size?: number }) =>
        c.get<Paginated<CardRow>>(`/admin/products/${seg(id)}/cards`, q),
    },
    orders: {
      list: (q: {
        status?: OrderStatus | ''
        q?: string
        page?: number
        page_size?: number
        /** NOT in contract (proposed): exact buyer filter for the inbox side panel. */
        buyer_id?: string
      }) => c.get<Paginated<AdminOrder>>('/admin/orders', q),
      get: (id: string) => c.get<AdminOrderDetail>(`/admin/orders/${seg(id)}`),
      changePrice: (id: string, amount: string, reason: string) =>
        c.patch<Order>(`/admin/orders/${seg(id)}/price`, { amount, reason }),
      deliver: (id: string, text: string, attachment_id?: string) =>
        c.post<Order>(`/admin/orders/${seg(id)}/deliver`, attachment_id ? { text, attachment_id } : { text }),
      cancel: (id: string, reason: string) =>
        c.post<Order>(`/admin/orders/${seg(id)}/cancel`, { reason }),
    },
    conversations: {
      list: (q: { filter?: ConversationFilter; status?: 'open' | 'closed'; page?: number; page_size?: number }) =>
        c.get<Paginated<Conversation>>('/admin/conversations', q),
      messages: (id: string, q: MessageQuery = {}) =>
        c.get<Message[]>(`/admin/conversations/${seg(id)}/messages`, { ...q }),
      send: (id: string, b: SendMessageBody) =>
        c.post<Message>(`/admin/conversations/${seg(id)}/messages`, b),
      assign: (id: string, agent_id: string | null) =>
        c.post<Conversation>(`/admin/conversations/${seg(id)}/assign`, { agent_id }),
      close: (id: string) => c.post<Conversation>(`/admin/conversations/${seg(id)}/close`),
      sendQuote: (id: string, b: QuoteInput) =>
        c.post<QuoteCardPayload>(`/admin/conversations/${seg(id)}/quotes`, b),
    },
    quotes: {
      withdraw: (id: string) => c.post<QuoteCardPayload>(`/admin/quotes/${seg(id)}/withdraw`),
    },
    agent: {
      getStatus: () => c.get<AgentStatus>('/admin/agent/status'),
      setStatus: (status: AgentStatusValue, message?: string | null) =>
        c.put<AgentStatus>('/admin/agent/status', message ? { status, message } : { status }),
      list: () => c.get<AgentInfo[]>('/admin/agents'),
    },
    ledger: (q: { page?: number; page_size?: number }) => c.get<Paginated<LedgerRow>>('/admin/ledger', q),
    audit: (q: { page?: number; page_size?: number }) => c.get<Paginated<AuditRow>>('/admin/audit', q),
    streamUrl: () => c.url('/admin/stream'),
  }
}
export type AdminApi = ReturnType<typeof createAdminApi>

/** Payment gateway hosts the buyer may be sent to (besides same-origin, e.g. the demo `/mockpay`). */
export const PAY_HOSTS: readonly string[] = ['zpayz.cn']

function currentBase(): string {
  return globalThis.location?.href ?? 'http://localhost/'
}

/**
 * Only allow navigating to a pay_url that is same-origin, or https on an allowlisted
 * gateway host. Defends against `javascript:`/`data:` URLs and open redirects / plain-http
 * phishing if the API were ever compromised.
 */
export function safePayUrl(raw: string, base = currentBase(), hosts: readonly string[] = PAY_HOSTS): string | null {
  try {
    const u = new URL(raw, base)
    const origin = new URL(base).origin
    if ((u.protocol === 'https:' || u.protocol === 'http:') && u.origin === origin) return u.toString()
    if (u.protocol === 'https:' && hosts.includes(u.hostname)) return u.toString()
    return null
  } catch {
    return null
  }
}

/**
 * URL for user-generated assets (attachments, covers) used in `href`/`src`/previews.
 * Same-origin (any scheme the page itself uses) or https only — never `javascript:`, `data:`, `blob:` from the server.
 */
export function safeAssetUrl(raw: string | null | undefined, base = currentBase()): string | null {
  if (!raw) return null
  try {
    const u = new URL(raw, base)
    const origin = new URL(base).origin
    if ((u.protocol === 'https:' || u.protocol === 'http:') && u.origin === origin) return u.toString()
    if (u.protocol === 'https:') return u.toString()
    return null
  } catch {
    return null
  }
}

/**
 * Links from product data (redeem URLs): https anywhere, or a same-origin relative path.
 * Never `http:`, `javascript:`, `data:`, protocol-relative `//host` or backslash tricks.
 */
export function safeLinkUrl(raw: string | null | undefined, base = currentBase()): string | null {
  if (!raw) return null
  const s = raw.trim()
  if (!s || /[\u0000-\u001f\\]/.test(s)) return null
  try {
    if (s.startsWith('/')) {
      if (s.startsWith('//')) return null
      const u = new URL(s, base)
      return u.origin === new URL(base).origin ? u.toString() : null
    }
    const u = new URL(s)
    return u.protocol === 'https:' && !u.username && !u.password ? u.toString() : null
  } catch {
    return null
  }
}

/** Accepts `{secrets: string[]}` (orders/{id}/reveal) or `{cards:[{code}]}` (lookup/reveal). */
export function revealedCodes(r: { secrets?: unknown; cards?: unknown } | null | undefined): string[] {
  if (!r) return []
  if (Array.isArray(r.secrets)) return r.secrets.filter((x): x is string => typeof x === 'string')
  if (Array.isArray(r.cards))
    return r.cards
      .map((c) => (c && typeof c === 'object' ? (c as { code?: unknown }).code : null))
      .filter((x): x is string => typeof x === 'string')
  return []
}

/** Only allow internal redirects (`/x`, not `//evil` or `https://...`). */
export function safeRedirect(raw: unknown, fallback = '/'): string {
  if (typeof raw !== 'string') return fallback
  if (!raw.startsWith('/') || raw.startsWith('//') || raw.startsWith('/\\')) return fallback
  return raw
}
