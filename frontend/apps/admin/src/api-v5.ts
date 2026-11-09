/**
 * Admin API + types for review-v5 §12 (settings center, categories, promo codes, resellers, import,
 * audit filters). Coded against the contract; only uses the shared `ApiClient`.
 */
import { seg, type ApiClient, type I18nText, type Paginated } from '@24goods/shared'
import type { AdminSiteV4, ChannelCfg, QuickQuestions, RateLimitCfg } from './api-v4'
import type { AdminOrder, Order } from '@24goods/shared'
import type { AdminProductV3 } from './api-v3'
import type { AdminProductV4, ContentImage } from './api-v4'

// ---------------- settings sections (§3; mirrors backend DEFAULTS) ----------------
export type PartialI18n = Partial<I18nText>
export interface GeneralCfg {
  site_name_i18n: PartialI18n
  subtitle_i18n: PartialI18n
  logo_url: string
  contact_i18n: PartialI18n
  footer_i18n: PartialI18n
}
export interface LegalCfg {
  enabled: boolean
  mode: 'checkbox' | 'modal'
  page_slugs: string[]
  updated_at: string
}
export interface FeaturesCfg {
  registration: boolean
  guest_checkout: boolean
  cart: boolean
  promo_codes: boolean
  resellers: boolean
  voice_messages: boolean
  lookup: boolean
  google_login: boolean
}
export const FEATURE_KEYS = [
  'registration',
  'guest_checkout',
  'cart',
  'promo_codes',
  'resellers',
  'voice_messages',
  'lookup',
  'google_login',
] as const satisfies readonly (keyof FeaturesCfg)[]
export const TURNSTILE_ACTIONS = [
  'payment_initiation', 'site_visit',
  'login',
  'register',
  'instant_account',
  'password_reset',
  'lookup',
  'promo_validate',
  'reseller_apply',
] as const
export interface SecurityCfg {
  payment_challenge_enabled: boolean
  site_visit_enabled: boolean
  challenge_preset: 'low'|'standard'|'high'
  turnstile_enabled: boolean
  turnstile_site_key: string
  turnstile_actions: string[]
  email_verify_on_register: boolean
  google_client_id: string
  staff_ip_allowlist: string[]
}
export interface DefaultsCfg {
  locale: 'en' | 'zh-CN'
  max_pending_orders: number
  order_timeout_minutes: number
}
export interface EmailCfg {
  enabled: boolean
  host: string
  port: number
  username: string
  from_addr: string
  from_name: string
  security: 'starttls' | 'ssl' | 'none'
  notify_order_paid: boolean
  notify_order_delivered: boolean
}
export interface DeliveryCfg {
  manual_eta_hours: number
  manual_note_i18n: PartialI18n
}
export interface MarkupCfg {
  markup_kind: 'percent' | 'fixed'
  /** percent: permille; fixed: minor units */
  markup_value: number
  round_to_minor: number
}
export interface ResellersCfg {
  base_domain: string
  link_mode: 'subdomain' | 'query'
  signup_fee_minor: number
  default_markup_kind: 'percent' | 'fixed'
  default_markup_value: number
  max_markup_permille: number
  max_markup_fixed_minor: number
  min_payout_minor: number
  settle_after_hours: number
  reserved_slugs: string[]
  custom_domains_enabled: boolean
  cf_zone_id: string
  fallback_origin: string
  max_domains: number
}
export interface AdminResellerDomain {
  id: string
  hostname: string
  reseller_slug: string
  status: 'pending' | 'active' | 'failed' | 'deleted'
  ssl_status: string | null
  created_at: string
  verified_at: string | null
}
export interface NotificationsCfg {
  telegram_enabled: boolean
  telegram_chat_ids: string[]
  event_order_paid: boolean
  event_chat_message: boolean
  event_after_sales: boolean
  event_payment_review: boolean
  event_reseller_application: boolean
  event_low_stock: boolean
}
export const TG_EVENTS = ['order_paid', 'chat_message', 'after_sales', 'payment_review', 'reseller_application', 'low_stock'] as const
export interface AuditCfg {
  retention_days: number
}
export interface SeoCfg {
  indexing_enabled: boolean
  index_storefronts: boolean
  default_description_i18n: Partial<I18nText>
  og_image: string
}
export interface EmailTemplate {
  subject_i18n: Partial<I18nText>
  body_i18n: Partial<I18nText>
}
export type EmailTemplates = Record<'verify_code' | 'password_reset' | 'order_paid' | 'order_delivered', EmailTemplate>
export type SecretName = 'turnstile_secret' | 'google_client_secret' | 'smtp_password' | 'telegram_bot_token' | 'cloudflare_saas_token'
export interface SecretState {
  configured: boolean
  last4?: string | null
}
export interface ZpayStatus {
  configured: boolean
  pid_last4?: string | null
}
export interface SiteSections {
  general: GeneralCfg
  legal: LegalCfg
  features: FeaturesCfg
  security: SecurityCfg
  defaults: DefaultsCfg
  email: EmailCfg
  notifications: NotificationsCfg
  delivery: DeliveryCfg
  import: MarkupCfg
  resellers: ResellersCfg
  audit: AuditCfg
  seo: SeoCfg
}
export type AdminSiteV5 = AdminSiteV4 &
  Partial<SiteSections> & {
    secrets?: Partial<Record<SecretName, SecretState>>
    /** effective values (defaults filled in) */
    email_templates?: EmailTemplates
    /** read-only status of the server-configured ZPay channel (never a secret) */
    zpay?: ZpayStatus | null
    bepusdt?: { configured: boolean } | null
  }
/** PUT /admin/site body: any subset of the sections (each field optional). */
export type SiteSectionPatch = Partial<{ [K in keyof SiteSections]: Partial<SiteSections[K]> }> &
  Partial<{
    /** null resets that template to its default */
    email_templates: Partial<Record<keyof EmailTemplates, EmailTemplate | null>>
    announcement: { enabled: boolean; display?: 'modal' | 'banner' | 'both'; require_ack?: boolean; title_i18n: I18nText; body_i18n: I18nText }
    after_sales_default_hours: number
    after_sales_default: { unit: 'month' | 'hour'; value: number }
    auto_confirm_hours: { manual: number; auto_card: number }
    support_hours: I18nText
    payments: { channels: Record<string, ChannelCfg> }
    support: { welcome_i18n: I18nText; quick_questions_i18n: QuickQuestions }
    rate_limit: RateLimitCfg
  }>

/** `error` codes of POST /admin/site/email/test (ok:false); anything else is shown as sent. */
export const EMAIL_TEST_ERRORS = ['connect_failed', 'blocked_host', 'auth_failed', 'rejected', 'send_failed'] as const
export const emailTestErrorKey = (code: string | undefined): string | null =>
  code && (EMAIL_TEST_ERRORS as readonly string[]).includes(code) ? `v5.email.err.${code}` : null

// ---------------- categories (§2) ----------------
export interface AdminCategory {
  id: string
  slug: string
  name_i18n: I18nText
  icon: string | null
  sort: number
  enabled: boolean
  product_count?: number
}
/** Raw shape of GET /admin/categories (`products` is the product count). */
type RawCategory = Omit<AdminCategory, 'product_count'> & { products?: number; product_count?: number }
const catOf = (c: RawCategory): AdminCategory => {
  const { products, ...rest } = c
  return { ...rest, product_count: c.product_count ?? products }
}
export type AdminCategoryInput = Pick<AdminCategory, 'name_i18n' | 'icon' | 'sort' | 'enabled'> & { slug?: string }

// ---------------- products (§1, §8) ----------------
export type AdminProductStatus = 'active' | 'draft' | 'archived'
export type AdminProductV5 = Omit<AdminProductV4, 'status'> & {
  status: AdminProductStatus
  archived_at?: string | null
  delivery_eta_hours?: number | null
  /** staff only, minor units */
  cost_minor?: number | null
  source_url?: string | null
}
/** Prefill for a new product (from the import dialog). */
export interface ProductPrefill {
  title: string
  description_md: string
  price_minor: number | null
  cost_minor: number | null
  source_url: string | null
  /** Our own /api/v1/files URLs (already fetched through the SSRF-safe importer); the first becomes the cover. */
  images?: string[]
}
export type BulkAction = 'publish' | 'unpublish' | 'archive' | 'restore' | 'set_category'
export interface ImportImage {
  url: string
  alt: string
}
export interface ImportDraft {
  title: string
  description_md: string
  images: ImportImage[]
  price_minor: number | null
  currency: string
  source_url: string | null
  site: string | null
  suggested_price_minor: number | null
  warnings: string[]
}

// ---------------- promo codes (§6) ----------------
export interface AdminPromo {
  id: string
  code: string
  kind: 'percent' | 'fixed'
  /** percent: permille; fixed: minor units */
  value: number
  currency: string
  min_subtotal_minor: number
  max_discount_minor: number | null
  starts_at: string | null
  ends_at: string | null
  usage_limit: number | null
  per_user_limit: number
  used_count: number
  product_ids: string[]
  category_slugs: string[]
  enabled: boolean
  note: string | null
  created_at?: string
}
export type AdminPromoInput = Omit<AdminPromo, 'id' | 'used_count' | 'currency' | 'created_at'>
export interface PromoRedemption {
  id: string
  user: string | null
  order_no: string | null
  discount_minor: number
  status: 'reserved' | 'used' | 'released'
  created_at: string
}

// ---------------- resellers (§7) ----------------
export type ResellerStatus = 'pending_payment' | 'active' | 'suspended'
export interface AdminReseller {
  id: string
  slug: string
  store_name: string
  status: ResellerStatus
  markup_kind: 'percent' | 'fixed'
  markup_value: number
  user_email?: string | null
  store_url?: string | null
  activated_at?: string | null
  created_at?: string
  stats?: {
    orders?: number
    revenue_minor?: number
    markup_minor?: number
    pending_minor?: number
    available_minor?: number
    paid_minor?: number
  }
}
export type PayoutStatus = 'requested' | 'paid' | 'rejected'
export interface AdminPayout {
  id: string
  reseller_id: string
  store_name?: string | null
  slug?: string | null
  amount_minor: number
  currency: string
  status: PayoutStatus
  account_snapshot: { method?: string; name?: string; account?: string } | null
  note: string | null
  /** v8: markup of this reseller held back by open after-sales tickets */
  disputed_minor?: number
  decided_at: string | null
  created_at: string
}

// ---------------- audit (§9) ----------------
export interface AuditFilters {
  action?: string
  actor?: string
  target?: string
  severity?: string
  since?: string
  until?: string
}
export interface AuditRowV5 {
  id?: string
  at: string
  actor: string
  action: string
  target: string
  ip: string | null
  severity?: string | null
  before?: unknown
  after?: unknown
  detail?: unknown
}

// ---------------- orders (§12) ----------------
export interface OrderV5Fields {
  kind: string
  discount_minor: number
  promo_code: string | null
  reseller: { slug?: string; store_name: string } | null
  reseller_markup_minor: number
  cost_minor: number | null
  overdue_manual?: boolean
  delivery_eta_hours?: number | null
  delivery_deadline?: string | null
  /** v9 §1.3: payment attempts whose outcome is unknown; staff can resolve them. */
  unresolved_attempts?: { id: string; status: string; created_at: string }[]
}
export type AdminOrderV5 = AdminOrder & Partial<OrderV5Fields>
export type OrderDetailV5 = Order & Partial<OrderV5Fields>

// ---- adapters: the backend nests some fields the console reads flat ----
interface RawStats {
  total?: { orders?: number; earnings_minor?: number }
  balances?: { pending_minor?: number; available_minor?: number; paid_minor?: number }
}
type RawReseller = Omit<AdminReseller, 'stats'> & { user?: { id?: string; email?: string } | null; stats?: RawStats | AdminReseller['stats'] }
const resellerOf = (r: RawReseller): AdminReseller => {
  const st = r.stats as RawStats | undefined
  const flat: AdminReseller['stats'] = st
    ? {
        orders: st.total?.orders,
        markup_minor: st.total?.earnings_minor,
        pending_minor: st.balances?.pending_minor,
        available_minor: st.balances?.available_minor,
        paid_minor: st.balances?.paid_minor,
      }
    : undefined
  const { user, stats: _s, ...rest } = r
  void _s
  return { ...rest, user_email: r.user_email ?? user?.email ?? null, stats: flat }
}
type RawPayout = Omit<AdminPayout, 'reseller_id' | 'account_snapshot'> & {
  reseller_id?: string
  reseller?: { id: string; slug: string; store_name: string }
  account?: AdminPayout['account_snapshot']
  account_snapshot?: AdminPayout['account_snapshot']
}
const payoutOf = (p: RawPayout): AdminPayout => {
  const { reseller, account, ...rest } = p
  return {
    ...rest,
    reseller_id: p.reseller_id ?? reseller?.id ?? '',
    store_name: p.store_name ?? reseller?.store_name ?? null,
    slug: p.slug ?? reseller?.slug ?? null,
    account_snapshot: p.account_snapshot ?? account ?? null,
  }
}
type RawRedemption = Omit<PromoRedemption, 'user'> & { user: string | { id?: string; email?: string } | null }
const redemptionOf = (r: RawRedemption): PromoRedemption => ({
  ...r,
  user: typeof r.user === 'string' || r.user == null ? (r.user ?? null) : r.user.email || r.user.id || null,
})
const mapPage = <A, B>(p: Paginated<A>, f: (a: A) => B): Paginated<B> => ({ ...p, items: p.items.map(f) })

function clean(q: object): Record<string, string | number | boolean | undefined> {
  const out: Record<string, string | number | boolean | undefined> = {}
  for (const [k, v] of Object.entries(q)) if (v !== '' && v !== null && v !== undefined) out[k] = v as string | number | boolean
  return out
}

// ---------------- v5.2: user suspension + blocked IPs ----------------
export interface AdminUserRow {
  id: string
  email: string
  handle: string | null
  display_name: string
  role: 'buyer' | 'agent' | 'admin'
  status: 'active' | 'suspended'
  suspended_until: string | null
  suspend_reason: string | null
  created_at: string
  last_login_at: string | null
}
export interface BlockedIp {
  id: string
  cidr: string
  note: string | null
  expires_at: string | null
  created_at: string
  created_by: string | null
}

export const API_TOKEN_SCOPES = ['products:read', 'products:write', 'cards:import', 'orders:read', 'chat', 'uploads', 'stats'] as const
export type ApiTokenScope = (typeof API_TOKEN_SCOPES)[number]
export interface ApiTokenRow {
  id: string
  name: string
  prefix: string
  scopes: ApiTokenScope[]
  expires_at: string | null
  last_used_at: string | null
  last_used_ip: string | null
  revoked_at: string | null
  created_at: string
  status: 'active' | 'revoked' | 'expired'
}

export function createAdminV5Api(c: ApiClient) {
  return {
    apiTokens: {
      list: () => c.get<{ items: ApiTokenRow[] }>('/admin/api-tokens'),
      /** The plaintext `token` is in this response only, once. */
      create: (b: { name: string; scopes: ApiTokenScope[]; expires_in_days: number }) =>
        c.post<ApiTokenRow & { token: string }>('/admin/api-tokens', b),
      revoke: (id: string) => c.post<ApiTokenRow>(`/admin/api-tokens/${seg(id)}/revoke`),
    },
    users: {
      list: (q: { q?: string; role?: string; status?: string; page?: number; page_size?: number }) =>
        c.get<Paginated<AdminUserRow>>('/admin/users', clean(q)),
      suspend: (id: string, b: { reason: string; until?: string | null }) =>
        c.post<AdminUserRow>(`/admin/users/${seg(id)}/suspend`, b.until ? b : { reason: b.reason }),
      unsuspend: (id: string) => c.post<AdminUserRow>(`/admin/users/${seg(id)}/unsuspend`),
    },
    blockedIps: {
      list: (q: { page?: number; page_size?: number }) => c.get<Paginated<BlockedIp>>('/admin/blocked-ips', clean(q)),
      add: (b: { cidr: string; note?: string; expires_at?: string | null }) =>
        c.post<BlockedIp>('/admin/blocked-ips', { cidr: b.cidr, note: b.note || undefined, expires_at: b.expires_at || undefined }),
      remove: (id: string) => c.delete<void>(`/admin/blocked-ips/${seg(id)}`),
    },
    site: {
      get: () => c.get<AdminSiteV5>('/admin/site'),
      put: (b: SiteSectionPatch) => c.put<AdminSiteV5>('/admin/site', b),
      /** value null deletes the secret. Never echoed back. */
      setSecret: (name: SecretName, value: string | null) => c.put<unknown>('/admin/site/secrets', { name, value }),
      testTelegram: () => c.post<{ ok: boolean; error?: string }>('/admin/site/telegram/test'),
      testEmail: (to: string, template?: string) =>
        c.post<{ ok: boolean; error?: string }>('/admin/site/email/test', template ? { to, template } : { to }),
    },
    categories: {
      list: () => c.get<RawCategory[] | { items: RawCategory[] }>('/admin/categories').then((r) => (Array.isArray(r) ? r : r.items).map(catOf)),
      create: (b: AdminCategoryInput & { slug: string }) => c.post<RawCategory>('/admin/categories', b).then(catOf),
      update: (id: string, b: Partial<AdminCategoryInput>) => c.patch<RawCategory>(`/admin/categories/${seg(id)}`, b).then(catOf),
      remove: (id: string) => c.delete<void>(`/admin/categories/${seg(id)}`),
    },
    products: {
      list: (q: { q?: string; status?: string; category?: string; page?: number; page_size?: number }) =>
        c.get<Paginated<AdminProductV5>>('/admin/products', clean(q)),
      archive: (id: string) => c.delete<void>(`/admin/products/${seg(id)}`),
      hardDelete: (id: string) => c.delete<void>(`/admin/products/${seg(id)}`, { query: { hard: true } }),
      restore: (id: string) => c.post<AdminProductV5>(`/admin/products/${seg(id)}/restore`),
      bulk: (b: { ids: string[]; action: BulkAction; category?: string }) =>
        c.post<{ updated: number }>('/admin/products/bulk', b),
      importFetch: (url: string) => c.post<ImportDraft>('/admin/products/import/fetch', { url }),
      importParse: (b: { text?: string; html?: string; base_url?: string }) =>
        c.post<ImportDraft>('/admin/products/import/parse', b),
    },
    content: {
      fetchImage: (url: string) => c.post<ContentImage>('/admin/content/images/fetch', { url }),
    },
    promos: {
      list: (q: { page?: number; page_size?: number }) => c.get<Paginated<AdminPromo>>('/admin/promo-codes', q),
      create: (b: AdminPromoInput) => c.post<AdminPromo>('/admin/promo-codes', b),
      update: (id: string, b: Partial<AdminPromoInput>) => c.patch<AdminPromo>(`/admin/promo-codes/${seg(id)}`, b),
      redemptions: (id: string, q: { page?: number; page_size?: number }) =>
        c.get<Paginated<RawRedemption>>(`/admin/promo-codes/${seg(id)}/redemptions`, q).then((r) => mapPage(r, redemptionOf)),
    },
    resellers: {
      list: (q: { status?: string; q?: string; page?: number; page_size?: number }) =>
        c.get<Paginated<RawReseller>>('/admin/resellers', clean(q)).then((r) => mapPage(r, resellerOf)),
      get: (id: string) => c.get<RawReseller>(`/admin/resellers/${seg(id)}`).then(resellerOf),
      update: (id: string, b: { status?: ResellerStatus; markup_kind?: 'percent' | 'fixed'; markup_value?: number }) =>
        c.patch<RawReseller>(`/admin/resellers/${seg(id)}`, b).then(resellerOf),
    },
    resellerDomains: {
      list: () => c.get<AdminResellerDomain[]>('/admin/reseller-domains'),
      remove: (id: string) => c.delete<void>(`/admin/reseller-domains/${seg(id)}`),
    },
    payouts: {
      list: (q: { status?: string; page?: number; page_size?: number }) =>
        c.get<Paginated<RawPayout>>('/admin/payouts', clean(q)).then((r) => mapPage(r, payoutOf)),
      decide: (id: string, b: { action: 'paid' | 'rejected'; note: string }) =>
        c.post<RawPayout>(`/admin/payouts/${seg(id)}/decide`, b).then(payoutOf),
    },
    audit: {
      list: (q: AuditFilters & { page?: number; page_size?: number }) =>
        c.get<Paginated<AuditRowV5>>('/admin/audit', clean(q)),
      csvUrl: (q: AuditFilters) => {
        const p = new URLSearchParams()
        for (const [k, v] of Object.entries(clean(q))) p.set(k, String(v))
        const s = p.toString()
        return c.url('/admin/audit.csv') + (s ? `?${s}` : '')
      },
    },
    orders: {
      list: (q: { status?: string; q?: string; reseller_id?: string; overdue_manual?: boolean; channel?: string; page?: number; page_size?: number }) =>
        c.get<Paginated<AdminOrderV5>>('/admin/orders', clean(q)),
    },
  }
}
export type AdminV5Api = ReturnType<typeof createAdminV5Api>
export type { AdminProductV3 }
