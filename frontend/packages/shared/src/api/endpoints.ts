import type { ApiClient } from './client'
import { seg } from './client'
import type {
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
  kind: 'image' | 'voice'
  /** voice only, ≤ 60000 */
  durationMs?: number
  filename?: string
}

export function uploadForm(file: Blob, o: UploadOptions): FormData {
  const fd = new FormData()
  const name = o.filename ?? (o.kind === 'voice' ? 'voice' : 'image')
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
      register: (b: { email: string; password: string; display_name: string; locale: Locale }) =>
        c.post<User>('/auth/register', b, { skipAuthHandler: true }),
      login: (b: { email: string; password: string }) =>
        c.post<User>('/auth/login', b, { skipAuthHandler: true }),
      logout: () => c.post<void>('/auth/logout', undefined, { skipAuthHandler: true }),
      me: () => c.get<User>('/auth/me', undefined, { skipAuthHandler: true }),
    },
    categories: () => c.get<Category[]>('/categories'),
    products: (q: { q?: string; category?: string; page?: number; page_size?: number }) =>
      c.get<Paginated<ProductSummary>>('/products', q),
    product: (id: string) => c.get<Product>(`/products/${seg(id)}`),
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
      create: (product_id: string, qty: number) => c.post<Order>('/orders', { product_id, qty }),
      list: (q: { page?: number; page_size?: number } = {}) => c.get<Paginated<Order>>('/orders', q),
      get: (id: string) => c.get<Order>(`/orders/${seg(id)}`),
      pay: (id: string, channel: string = DEFAULT_PAY_CHANNEL) =>
        c.post<{ pay_url: string }>(`/orders/${seg(id)}/pay`, { channel }),
      reveal: (id: string) => c.post<{ secrets: string[] }>(`/orders/${seg(id)}/reveal`),
      confirm: (id: string) => c.post<Order>(`/orders/${seg(id)}/confirm`),
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

/** Only allow internal redirects (`/x`, not `//evil` or `https://...`). */
export function safeRedirect(raw: unknown, fallback = '/'): string {
  if (typeof raw !== 'string') return fallback
  if (!raw.startsWith('/') || raw.startsWith('//') || raw.startsWith('/\\')) return fallback
  return raw
}
