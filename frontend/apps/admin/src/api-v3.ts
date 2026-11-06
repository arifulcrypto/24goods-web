/**
 * Admin endpoints + types added by review-v3 §12 (MVP-0.5).
 *
 * Lives in the admin app (not packages/shared) while the buyer-web agent owns shared; the
 * orchestrator may move it to packages/shared/src/api/admin-v3.ts at merge. Only uses the
 * shared `ApiClient`, so it moves without changes.
 */
import {
  seg,
  type AdminOrderDetail,
  type AdminProduct,
  type AdminProductInput,
  type ApiClient,
  type Attachment,
  type DeliveryMode,
  type I18nText,
  type Order,
  type OrderStatus,
  type Paginated,
} from '@24goods/shared'
import type { CardFilters, CardStatusV3 } from './lib/cards'
import { buildCardQuery } from './lib/cards'
import type { TicketAction, TicketReason, TicketStatus } from './lib/afterSales'

// ---------------- products (§5) ----------------
export interface ProductDeliveryFields {
  delivery_format: string | null
  delivery_separator: string | null
  redeem_url: string | null
  usage_i18n: I18nText
  /** null = site default (672 h); 0 = no after-sales. */
  after_sales_hours: number | null
}
export type AdminProductV3 = AdminProduct & Partial<ProductDeliveryFields>
export type AdminProductInputV3 = AdminProductInput & Partial<ProductDeliveryFields>

// ---------------- cards (§8) ----------------
export interface CardRowV3 {
  id: string
  masked: string
  status: CardStatusV3
  batch_id: string | null
  expires_at: string | null
  void_reason: string | null
  order_no: string | null
  created_at: string
  /** Present on /admin/cards/find results (cross-product search). Optional elsewhere. */
  product_id?: string | null
  product_title_i18n?: I18nText | null
  /** 1-based position of the matching line among the non-blank submitted lines (find only). */
  line?: number
  order_id: string | null
}
export interface CardBatch {
  id: string
  note: string | null
  count: number
  expires_at: string | null
  created_at: string
  created_by?: string | null
  available: number
  sold: number
  void: number
  reserved: number
  expiring_7d: number
}
export interface CardImportResultV3 {
  imported: number
  duplicates: number
  rejected: number
  batch_id: string | null
}
export type FindBy = 'content' | 'first_field'

// ---------------- after-sales (§7) ----------------
export interface AfterSalesTicket {
  id: string
  order_id: string
  order_no?: string
  status: TicketStatus
  reason: TicketReason
  description: string
  attachments?: Attachment[]
  attachment_ids?: string[]
  resolution_note: string | null
  resolved_by?: string | null
  resolved_at: string | null
  created_at: string
  updated_at: string
  /** Snapshot of `orders.after_sales_until`. */
  after_sales_until?: string | null
  buyer?: { id: string; display_name: string; email?: string }
  order?: {
    id: string
    order_no: string
    title: string
    amount: string
    currency: string
    qty: number
    status: OrderStatus
    delivery_mode: DeliveryMode
    delivered_at: string | null
    after_sales_until?: string | null
  }
  conversation_id?: string | null
}
export interface AfterSalesActionBody {
  action: TicketAction
  note?: string
  delivery_text?: string
}

// ---------------- orders (§6, §7) ----------------
export interface DeliveryInfo {
  format_labels: string[]
  separator: string
  redeem_url: string | null
  usage_html: string | null
}
export interface OrderV3Fields {
  after_sales_until: string | null
  auto_confirm_at: string | null
  after_sales: AfterSalesTicket | null
  delivery_info: DeliveryInfo | null
}
export type AdminOrderDetailV3 = AdminOrderDetail & Partial<OrderV3Fields>
export type OrderV3 = Order & Partial<OrderV3Fields>

// ---------------- content (§2.3, §9) ----------------
export type ArticleKind = 'article' | 'page'
export type ArticleStatus = 'draft' | 'published'
export interface AdminArticle {
  id: string
  slug: string
  kind: ArticleKind
  category: string
  title_i18n: I18nText
  body_i18n: I18nText
  status: ArticleStatus
  pinned: boolean
  views: number
  published_at: string | null
  updated_at: string
  created_at?: string
}
export type AdminArticleInput = Pick<AdminArticle, 'slug' | 'kind' | 'category' | 'title_i18n' | 'body_i18n' | 'status' | 'pinned'>

export interface SiteAnnouncement {
  version: number
  enabled: boolean
  title_i18n: I18nText
  body_i18n: I18nText
  body_html_i18n?: I18nText
}
export interface AdminSiteSettings {
  announcement: SiteAnnouncement | null
  after_sales_default_hours: number
  auto_confirm_hours: { manual: number; auto_card: number }
  support_hours: I18nText
  /** Built-in fallbacks used when a value was never set. */
  defaults: { after_sales_default_hours: number; auto_confirm_hours: { manual: number; auto_card: number } }
}
export type AdminSiteSettingsInput = Omit<AdminSiteSettings, 'announcement' | 'defaults'> & {
  announcement: Omit<SiteAnnouncement, 'version'>
}

export function createAdminV3Api(c: ApiClient) {
  return {
    products: {
      list: (q: { q?: string; status?: string; page?: number; page_size?: number }) =>
        c.get<Paginated<AdminProductV3>>('/admin/products', q),
      create: (b: AdminProductInputV3) => c.post<AdminProductV3>('/admin/products', b),
      update: (id: string, b: Partial<AdminProductInputV3>) => c.patch<AdminProductV3>(`/admin/products/${seg(id)}`, b),
      batches: (id: string) => c.get<{ items: CardBatch[] }>(`/admin/products/${seg(id)}/batches`).then((r) => r.items),
      importCards: (id: string, b: { lines: string; note?: string; expires_at?: string }) =>
        c.post<CardImportResultV3>(`/admin/products/${seg(id)}/cards/import`, b),
      cards: (id: string, f: CardFilters, page: number, page_size: number) =>
        c.get<Paginated<CardRowV3>>(`/admin/products/${seg(id)}/cards`, buildCardQuery(f, page, page_size)),
    },
    cards: {
      /** Codes go in the JSON body only (never the URL). */
      find: (lines: string[], by: FindBy) => c.post<{ items: CardRowV3[]; missing: number }>('/admin/cards/find', { lines, by }),
      voidIds: (ids: string[], reason: string) => c.post<{ voided: number }>('/admin/cards/void', { ids, reason }),
      voidBatch: (batch_id: string, reason: string) => c.post<{ voided: number }>('/admin/cards/void', { batch_id, reason }),
      replace: (id: string, code: string) => c.post<CardRowV3>(`/admin/cards/${seg(id)}/replace`, { code }),
    },
    afterSales: {
      list: (q: { status?: TicketStatus | ''; page?: number; page_size?: number }) =>
        c.get<Paginated<AfterSalesTicket>>('/admin/after-sales', q),
      get: (id: string) => c.get<AfterSalesTicket>(`/admin/after-sales/${seg(id)}`),
      action: (id: string, b: AfterSalesActionBody) => c.post<AfterSalesTicket>(`/admin/after-sales/${seg(id)}/action`, b),
    },
    orders: {
      get: (id: string) => c.get<AdminOrderDetailV3>(`/admin/orders/${seg(id)}`),
    },
    articles: {
      list: (q: { q?: string; status?: ArticleStatus | ''; kind?: ArticleKind | ''; page?: number; page_size?: number }) =>
        c.get<Paginated<AdminArticle>>('/admin/articles', q),
      get: (id: string) => c.get<AdminArticle>(`/admin/articles/${seg(id)}`),
      create: (b: AdminArticleInput) => c.post<AdminArticle>('/admin/articles', b),
      update: (id: string, b: Partial<AdminArticleInput>) => c.patch<AdminArticle>(`/admin/articles/${seg(id)}`, b),
      remove: (id: string) => c.delete<void>(`/admin/articles/${seg(id)}`),
    },
    site: {
      get: () => c.get<AdminSiteSettings>('/admin/site'),
      put: (b: AdminSiteSettingsInput) => c.put<AdminSiteSettings>('/admin/site', b),
    },
  }
}
export type AdminV3Api = ReturnType<typeof createAdminV3Api>
