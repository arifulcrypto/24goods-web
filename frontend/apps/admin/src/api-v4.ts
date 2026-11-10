/**
 * Admin-only additions for review-v4 §12. Lives in the admin app (packages/shared belongs to the
 * web agent this batch). Only uses the shared `ApiClient`.
 */
import { seg, type ApiClient, type I18nText, type Message, type Order } from '@24goods/shared'
import type { AdminOrderDetailV3, AdminProductV3, AdminSiteSettings, AdminSiteSettingsInput } from './api-v3'

// ---------------- §4 order-information popup (admin i18n form) ----------------
export interface BuyerInputForm {
  enabled: boolean
  required: boolean
  multiline: boolean
  /** 1–4000 */
  max_len: number
  title_i18n: I18nText
  prompt_i18n: I18nText
  placeholder_i18n: I18nText
}
export const BUYER_INPUT_DEFAULT: BuyerInputForm = {
  enabled: false,
  required: true,
  multiline: true,
  max_len: 2000,
  title_i18n: { en: '', 'zh-CN': '' },
  prompt_i18n: { en: '', 'zh-CN': '' },
  placeholder_i18n: { en: '', 'zh-CN': '' },
}
export type AdminProductV4 = AdminProductV3 & { buyer_input?: BuyerInputForm | null; description_html?: string | null }
export type AdminProductInputV4 = { buyer_input?: BuyerInputForm | null }

// ---------------- §1/§3/§4 order views ----------------
export interface SiblingOrder {
  id: string
  order_no: string
  title: string
  amount: string
  currency: string
  status: Order['status']
}
export interface OrderV4Fields {
  subtotal: string
  fee: string
  /** e.g. "3%" */
  fee_rate: string
  checkout_no: string | null
  /** owner/staff only; never logged. */
  buyer_input: string | null
  cancel_reason: string | null
  /** Optional: sibling orders of the same checkout, if the backend embeds them. */
  checkout_orders?: SiblingOrder[]
}
export type AdminOrderDetailV4 = AdminOrderDetailV3 & Partial<OrderV4Fields>
export type OrderRowV4 = Order & Partial<OrderV4Fields>

// ---------------- §1/§5/§10 site settings ----------------
export const RATE_LIMIT_MESSAGE = 'Access is too frequent. Please try again later.'
export const RATE_LIMIT_MESSAGE_ZH = '访问过于频繁，请稍后再试'
export interface ChannelCfg {
  enabled: boolean
  surcharge_permille: number
}
export interface RateLimitCfg {
  enabled: boolean
  limit: number
  window_s: number
}
export type QuickQuestions = { en: string[]; 'zh-CN': string[] }
export interface SiteV4Fields {
  payments: { channels: Record<string, ChannelCfg> }
  support: { welcome_i18n: I18nText; quick_questions_i18n: QuickQuestions }
  rate_limit: RateLimitCfg
}
export type AdminSiteV4 = AdminSiteSettings & Partial<SiteV4Fields>
export type AdminSiteInputV4 = AdminSiteSettingsInput & SiteV4Fields

export const DEFAULT_SURCHARGE_PERMILLE = 30
export const MAX_SURCHARGE_PERMILLE = 200
export const MAX_QUICK_QUESTIONS = 6
export const MAX_QUICK_LEN = 60

export interface ContentImage {
  id: string
  url: string
}

export function createAdminV4Api(c: ApiClient) {
  return {
    site: {
      get: () => c.get<AdminSiteV4>('/admin/site'),
      put: (b: AdminSiteInputV4) => c.put<AdminSiteV4>('/admin/site', b),
    },
    orders: {
      get: (id: string) => c.get<AdminOrderDetailV4>(`/admin/orders/${seg(id)}`),
      /** reason is required (§3). */
      close: (id: string, reason: string) => c.post<Order>(`/admin/orders/${seg(id)}/cancel`, { reason }),
      /** v8: put a held auto-card order back on the delivery queue (admin). */
      requeueDelivery: (id: string) => c.post<Order>(`/admin/orders/${seg(id)}/requeue-delivery`),
      /** #72 B2: exit from manual_review — accept the recorded payment, or close when nothing was paid (admin). */
      releaseHold: (id: string, b: { outcome: 'accept' | 'close'; reason: string }) =>
        c.post<Order>(`/admin/orders/${seg(id)}/release-hold`, b),
      /** v8: refund a paid, undelivered order after the money was sent back (admin). */
      refund: (id: string, b: { reference: string; note?: string }) => c.post<Order>(`/admin/orders/${seg(id)}/refund`, b),
    },
    content: {
      uploadImage: (file: Blob, filename = 'image') => {
        const fd = new FormData()
        fd.append('file', file, filename)
        return c.upload<ContentImage>('/admin/content/images', fd)
      },
    },
    conversations: {
      /** The server builds the card payload from the product; the client sends only the id. */
      sendProductCard: (conversationId: string, product_id: string) =>
        c.post<Message>(`/admin/conversations/${seg(conversationId)}/messages`, {
          client_msg_id: crypto.randomUUID(),
          type: 'product_card',
          product_id,
        }),
    },
  }
}
export type AdminV4Api = ReturnType<typeof createAdminV4Api>
