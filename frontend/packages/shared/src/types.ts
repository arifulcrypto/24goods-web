/**
 * Contract types — copied verbatim from docs/api-contract.md (MVP-0).
 * A change here must be mirrored in the contract and the backend.
 */

export type Locale = 'en' | 'zh-CN'
export type Role = 'buyer' | 'agent' | 'admin'
export interface User {
  id: string
  email: string
  display_name: string
  role: Role
  locale: Locale
  /** v3: one-tap instant account (random handle + password, email optional). */
  is_instant?: boolean
  email_verified?: boolean
  /** v3: login handle `u-xxxxxxx` of instant accounts (usable as the login id). `email` is blank for them. */
  handle?: string | null
  /** v8b: same-origin avatar URL (authenticated), null = show the initial. */
  avatar_url?: string | null
  /** v11: generic preset id (`p01`..`p16`) when no picture is uploaded. */
  avatar_preset?: string | null
}

export type DeliveryMode = 'auto_card' | 'manual'

/** v11 W1: a purchasable SKU of a product (buyer view; prices already include the storefront markup). */
export interface ProductVariant {
  id: string
  name: string
  price: string
  /** auto_card: available cards; manual: reserved-stock counter (null = unlimited) */
  stock: number | null
  /** Resolved after-sales window in hours / whole months (variant terms, else the product's, else site default). */
  after_sales_hours?: number | null
  after_sales_months?: number | null
  /** The variant (or its product) is a subscription. */
  subscription?: boolean
}

export interface ProductSummary {
  id: string
  title: string
  cover: string | null
  price: string
  currency: string
  delivery_mode: DeliveryMode
  /** auto_card: available cards; manual: reserved-stock counter (null = unlimited) */
  stock: number | null
  category: string
  sold: number
  /** v3: resolved after-sales window in hours (site default applied); 0 = no after-sales. */
  after_sales_hours?: number | null
  /** v7: set when the resolved period is whole calendar months (hours is then months x 720, approximate). */
  after_sales_months?: number | null
  /** v5 §11: manual products only; promised shipping window in hours. */
  delivery_eta_hours?: number | null
  /** Order-information form (null = off). Lists carry it too, so quick buy asks for it (issue 49 B). */
  buyer_input?: BuyerInputConfig | null
  /** v11 W1: active variants (sorted). When non-empty, `price` is the lowest and `stock` the sum. */
  has_variants?: boolean
  variants?: ProductVariant[]
  /** v11 W1: the variants have different prices, so `price` reads "from ¥X". */
  price_from?: boolean
}
export type ProductStatus = 'active' | 'draft'
export interface Product extends ProductSummary {
  description: string
  images: string[]
  status: ProductStatus
  /** v3: field labels joined by `delivery_separator`, e.g. `Account----Password----2FA`. */
  delivery_format?: string | null
  delivery_separator?: string | null
  /** v3: https only (validated server-side, re-validated client-side). */
  redeem_url?: string | null
  /** v3: server-sanitized HTML (markdown → nh3). Only *_html fields may be rendered as HTML. */
  usage_html?: string | null
  /** v4: server-rendered, sanitized HTML of the markdown description (render with SafeHtml). */
  description_html?: string | null
}
/** v4 (§4): public config of the order-information popup. */
export interface BuyerInputConfig {
  enabled: boolean
  required: boolean
  multiline: boolean
  max_len: number
  title: string
  prompt: string
  placeholder: string
}
export type I18nText = Record<Locale, string>
export interface AdminProduct
  extends Omit<Product, 'title' | 'description' | 'buyer_input' | 'variants' | 'has_variants' | 'price_from'> {
  title_i18n: I18nText
  description_i18n: I18nText
  floor_price: string
  created_at: string
  /** v5.2 manual-delivery stock; null = unlimited */
  manual_stock?: number | null
  /** v11 W1: every variant, archived included (staff only). */
  variants?: AdminVariant[]
}
/** v11 W1: staff view of a variant. */
export interface AdminVariant {
  id: string
  name_i18n: I18nText
  price: string
  floor_price: string
  /** manual products: null = unlimited */
  manual_stock: number | null
  /** auto_card: available cards of this variant; manual: the counter */
  stock: number | null
  sort: number
  status: 'active' | 'archived'
  /** null = inherit the product's */
  after_sales_months: number | null
  after_sales_hours: number | null
  subscription: Record<string, unknown> | null
  sold: number
}
/** Body for POST/PATCH /admin/products/{id}/variants. */
export interface AdminVariantInput {
  name_i18n: I18nText
  price: string
  floor_price: string
  manual_stock?: number | null
  sort?: number
  status?: 'active' | 'archived'
  after_sales_months?: number | null
  after_sales_hours?: number | null
  subscription?: Record<string, unknown> | null
}
/** Body for POST /admin/products ("AdminProduct fields (no id)"), PATCH accepts a partial. */
export interface AdminProductInput {
  title_i18n: I18nText
  description_i18n: I18nText
  price: string
  floor_price: string
  currency: string
  delivery_mode: DeliveryMode
  category: string
  status: ProductStatus
  images?: string[]
  /** v4: i18n form of the order-information popup config. */
  buyer_input?: BuyerInputAdmin | null
}
export interface BuyerInputAdmin {
  enabled: boolean
  required: boolean
  multiline: boolean
  max_len: number
  title_i18n: Partial<I18nText>
  prompt_i18n: Partial<I18nText>
  placeholder_i18n: Partial<I18nText>
}

export interface Attachment {
  id: string
  url: string
  mime: string
  bytes: number
  kind: 'image' | 'voice' | 'file'
  /** v3 (kind=file): original file name. */
  name?: string
  width?: number
  height?: number
  duration_ms?: number
}

export type MessageType =
  | 'text'
  | 'image'
  | 'voice'
  | 'product_card'
  | 'quote_card'
  | 'order_card'
  | 'system'
  | 'file'

export type SenderRole = 'buyer' | 'agent' | 'system'
export interface Message {
  id: number
  conversation_id: string
  client_msg_id: string | null
  sender: { id: string | null; role: SenderRole; display_name: string; avatar_url?: string | null; avatar_preset?: string | null }
  type: MessageType
  text: string | null
  attachment: Attachment | null
  payload:
    | ProductCardPayload
    | QuoteCardPayload
    | OrderCardPayload
    | SystemPayload
    | FilePayload
    | RedactedPayload
    | MessageMeta
    | null
  created_at: string
}
/** v11 W2: quoted message snapshot stored in `payload.reply_to`. */
export interface ReplyRef {
  id: number
  sender_name: string
  sender_role: SenderRole
  /** ≤ 120 chars of text, or a `[image]`-style marker; empty after the original is recalled. */
  preview: string
  type: MessageType
}
/** v11 W2: optional flags carried by text/media payloads. */
export interface MessageMeta {
  reply_to?: ReplyRef
  edited_at?: string
  pinned?: boolean
  recalled?: boolean
}
/** SSE `typing` event (never stored). */
export interface TypingEvent {
  conversation_id: string
  role: SenderRole
  name: string
  until: string
}
/** v3: `type=file` message payload (text/plain attachment). */
export interface RedactedPayload {
  redacted: true
}
export interface FilePayload {
  name: string
  bytes: number
  /** First ~160 characters of the file. */
  preview: string
}
export interface ProductCardPayload {
  product_id: string
  title: string
  cover: string | null
  price: string
  currency: string
}
export type QuoteStatus = 'offered' | 'accepted' | 'withdrawn' | 'expired'
export interface QuoteCardPayload {
  quote_id: string
  title: string
  amount: string
  currency: string
  qty: number
  note: string | null
  status: QuoteStatus
  expires_at: string
  order_id: string | null
  /** v11 W2: the variant the quote was negotiated for (products with variants). */
  variant_id?: string | null
  variant_name?: string | null
}
export interface OrderCardPayload {
  order_id: string
  title: string
  amount: string
  currency: string
  status: OrderStatus
}
/** e.g. price_changed, delivered, agent_joined */
export interface SystemPayload {
  event: string
  [k: string]: unknown
}

export interface Conversation {
  id: string
  buyer: { id: string; display_name: string; avatar_url?: string | null; avatar_preset?: string | null }
  product: ProductCardPayload | null
  status: 'open' | 'closed'
  assigned_agent: { id: string; display_name: string; avatar_url?: string | null; avatar_preset?: string | null } | null
  last_message: Message | null
  unread: number
  updated_at: string
}

export type OrderStatus =
  | 'pending_payment'
  | 'paid'
  | 'delivered'
  | 'completed'
  | 'cancelled'
  | 'expired'
  | 'refunded'
  | 'manual_review'

export interface Order {
  id: string
  order_no: string
  title: string
  product_id: string | null
  quote_id: string | null
  qty: number
  amount: string
  currency: string
  status: OrderStatus
  delivery_mode: DeliveryMode
  channel: string | null
  created_at: string
  paid_at: string | null
  delivered_at: string | null
  expires_at: string | null
  version: number
  manual_delivery: { text: string | null; attachment: Attachment | null } | null
  /** auto_card && delivered/completed && owner */
  can_reveal: boolean
  // ---- v3 (review-v3 §12) — optional so older payloads still type-check ----
  /** Returned once by POST /orders (never again). */
  access_code?: string | null
  after_sales_until?: string | null
  auto_confirm_at?: string | null
  after_sales?: AfterSales | null
  /** v7: the service this order activated or renewed (own orders only), else null. */
  service?: { status: 'pending' | 'active' | 'cancelled'; starts_at: string | null; ends_at: string | null } | null
  delivery_info?: DeliveryInfo | null
  source?: string | null
  // ---- v4 (review-v4 §12) ----
  /** Money before the channel fee (same format as `amount`). Absent on pre-v4 payloads. */
  subtotal?: string
  fee?: string
  /** e.g. "3%" */
  fee_rate?: string
  /** Set on member orders of a combined checkout. */
  checkout_no?: string | null
  /** Owner/staff only. Never logged. */
  buyer_input?: string | null
  input_config?: BuyerInputConfig | null
  can_edit_input?: boolean
  cancel_reason?: string | null
  // ---- v5 (review-v5 §12) ----
  /** Money off, minor units. */
  discount_minor?: number
  kind?: string
  /** Buyer view of the storefront the order was placed on. */
  reseller?: { store_name: string } | null
  delivery_eta_hours?: number | null
  /** ISO time (manual orders): paid_at + eta. */
  delivery_deadline?: string | null
  /** v8: ISO time from which a paid, undelivered order may be reported as not received. */
  not_received_from?: string | null
  /** v8: what staff entered when confirming the money was sent back (refunded orders). */
  refund_reference?: string | null
  /** issue 23: server-computed fee/total per ENABLED channel (pending orders only; else null). */
  channel_totals?: ChannelTotals | null
  /** v11 W1: the variant bought (name as of purchase), null for products without variants. */
  variant_id?: string | null
  variant_name?: string | null
}

/** `{channel_id: {fee, fee_rate, amount}}`: money strings computed by the server (never the client). */
export type ChannelTotals = Record<string, { fee: string; fee_rate: string; amount: string }>

export type CheckoutStatus = 'pending_payment' | 'paid' | 'cancelled' | 'expired' | 'manual_review'
export interface CheckoutView {
  checkout_no: string
  status: CheckoutStatus
  amount: string
  currency: string
  expires_at: string | null
  order_nos: string[]
  subtotal: string
  fee: string
  channel_totals?: ChannelTotals | null
}
export interface CheckoutLineInput {
  product_id: string
  variant_id?: string
  qty: number
  buyer_input?: string
}
export interface CheckoutResult {
  checkout: CheckoutView
  orders: Order[]
  /** Returned once, keyed by order_no. */
  access_codes: Record<string, string>
}
export interface PaymentChannel {
  id: string
  label: string
  fee_rate: string
  permille: number
}
/** GET /pay/usdt/{no}: our own USDT pay page (display + polling; never trusted for fulfilment). */
export interface UsdtPayView {
  channel: string
  label: string
  network: string
  address: string
  /** exact USDT to send (decimal string) */
  usdt_amount: string
  fiat_amount_minor: number
  currency: string
  expires_at: number
  seconds_left: number
  expired: boolean
  /** order / checkout status */
  status: string
  /** server-generated QR (SVG markup) of the address: render via an <img> data URI only */
  qr_svg: string
  /** demo only: the BEpusdt simulator's pay page (null in production) */
  sim_url?: string | null
}
export interface CartQuoteLine {
  product_id: string
  /** v11 W1 */
  variant_id?: string | null
  variant_name?: string | null
  title: string
  cover: string | null
  unit_price: string
  currency: string
  stock: number | null
  available: boolean
  max_qty: number
  needs_input: boolean
  qty: number
  line_subtotal: string
  // ---- v5 ----
  discount_minor?: number
  unavailable_reason?: UnavailableReason | null
  delivery_mode?: DeliveryMode
  delivery_eta_hours?: number | null
}
export type UnavailableReason =
  | 'archived'
  | 'unpublished'
  | 'out_of_stock'
  | 'variant_required'
  | 'variant_unavailable'
export interface CartQuote {
  lines: CartQuoteLine[]
  discount_minor?: number
  promo?: { code: string; discount_minor: number } | { error: string } | null
  subtotal: string
  fee: string
  fee_rate: string
  total: string
  currency: string
  /** channel the quote was priced with + the totals for every enabled channel (per-line ceiling) */
  channel?: string
  channel_totals?: ChannelTotals
}

/** v3: how delivered codes are presented (from the product, snapshotted on the order). */
export interface DeliveryInfo {
  format_labels: string[]
  separator: string
  redeem_url: string | null
  usage_html: string | null
}

export type AfterSalesReason = 'not_received' | 'invalid_code' | 'wrong_item' | 'account_issue' | 'other'
export const AFTER_SALES_REASONS: readonly AfterSalesReason[] = [
  'not_received',
  'invalid_code',
  'wrong_item',
  'account_issue',
  'other',
]
export type AfterSalesStatus = 'open' | 'processing' | 'replaced' | 'refunded' | 'rejected' | 'resolved'
export interface AfterSales {
  id: string
  order_id: string
  reason: AfterSalesReason
  description: string
  attachment_ids?: string[]
  /** Optional expanded attachments (if the server includes them). */
  attachments?: Attachment[]
  status: AfterSalesStatus
  resolution_note: string | null
  created_at: string
  updated_at: string
  resolved_at?: string | null
  /** Staff views and SSE only. */
  order_no?: string
}
export interface AfterSalesInput {
  reason: AfterSalesReason
  description: string
  attachment_ids: string[]
}

/** v3: POST /lookup result — the order view without buyer identity. */
export interface LookupOrder {
  id?: string
  order_no: string
  title: string
  qty: number
  amount: string
  currency: string
  status: OrderStatus
  delivery_mode: DeliveryMode
  created_at: string
  paid_at: string | null
  delivered_at: string | null
  manual_delivery?: { text: string | null; attachment: Attachment | null } | null
  can_reveal?: boolean
  after_sales_until?: string | null
  auto_confirm_at?: string | null
  delivery_info?: DeliveryInfo | null
}

// ---- v3 site / content / auth ----
export interface Announcement {
  version: string | number
  title: string
  body_html: string
  enabled?: boolean
  /** v9: how it is shown (default modal) and whether the buyer must acknowledge it. */
  display?: 'modal' | 'banner' | 'both'
  require_ack?: boolean
}
/** v11 W3: announcement (news) list/detail item. `read` is null for guests. */
export interface AnnouncementItem {
  id: string
  title: string
  category: 'news' | 'operations' | 'inventory' | 'notice'
  pinned: boolean
  published_at: string | null
  read?: boolean | null
}
export interface AnnouncementDetail extends AnnouncementItem {
  body_html: string
}
export interface AnnouncementsSite {
  prompt_new: boolean
  show_badge: boolean
  /** null for guests (their read state lives in localStorage). */
  unread: number | null
  newest: AnnouncementItem | null
}
export type CatalogSort = 'manual' | 'sales' | 'price_asc' | 'price_desc' | 'newest'
export const CATALOG_SORTS = ['manual', 'sales', 'price_asc', 'price_desc', 'newest'] as const satisfies readonly CatalogSort[]
export interface SiteInfo {
  brand: string
  /** v11 W3: news bell + prompt (separate from the single banner above). */
  announcements?: AnnouncementsSite
  catalog?: { default_sort: CatalogSort }
  announcement: Announcement | null
  after_sales_default_hours: number
  after_sales_default?: { unit: 'month' | 'hour'; value: number }
  support_hours: string | null
  pages: { slug: string; title: string }[]
  /** v4: welcome bubble + quick-question chips (resolved to the request locale). */
  support?: { welcome?: string | null; quick_questions?: string[] } | null
  // ---- v5 (review-v5 §3, §12): all optional so older payloads still type-check ----
  features?: Partial<SiteFeatures>
  general?: { site_name_i18n?: Partial<I18nText>; subtitle_i18n?: Partial<I18nText>; logo_url?: string } | null
  legal?: { enabled: boolean; mode: 'checkbox' | 'modal'; pages: { slug: string; title: string }[] } | null
  turnstile?: { enabled: boolean; site_key: string; actions: string[] } | null
  google_login?: boolean
  delivery?: { manual_eta_hours?: number; manual_note?: string | null } | null
  storefront?: Storefront | null
  /** Optional: reseller program public info (base domain for the slug preview). */
  resellers?: { base_domain?: string; link_mode?: 'subdomain' | 'query'; signup_fee_minor?: number } | null
  /** Optional (security.email_verify_on_register); the 422 `email_code_required` is the fallback. */
  email_verify_on_register?: boolean
  /** v11 W2: admin defaults for buyer chat alerts; the buyer's own choice (localStorage) wins. */
  notify_defaults?: { buyer?: { sound?: boolean; desktop?: boolean } }
}
export interface SiteFeatures {
  registration: boolean
  guest_checkout: boolean
  cart: boolean
  promo_codes: boolean
  resellers: boolean
  voice_messages: boolean
  lookup: boolean
  google_login: boolean
}
export interface Storefront {
  slug: string
  store_name: string
  logo_url: string | null
  welcome: string | null
  /** Owner rule: storefronts are white-label (no platform brand anywhere). Treated as true unless explicitly false. */
  white_label?: boolean
}

// ---- v5 promo (§6) ----
export interface PromoPreview {
  code: string
  discount_minor: number
  eligible_subtotal_minor: number
  description: string | null
}

// ---- v5 reseller center (§7) ----
export type ResellerStatus = 'pending_payment' | 'active' | 'suspended'
export type MarkupKind = 'percent' | 'fixed'
export interface PayoutAccount {
  method: 'alipay'
  name: string
  account: string
}
export interface ResellerMe {
  slug: string
  store_name: string
  status: ResellerStatus
  store_url: string
  markup_kind: MarkupKind
  /** percent: permille (100 = 10 %); fixed: minor units */
  markup_value: number
  logo_url: string | null
  welcome_i18n: Partial<I18nText>
  contact: string | null
  payout_account: PayoutAccount | null
  signup_order_no: string | null
  balances: { pending_minor: number; available_minor: number; paid_minor: number; disputed_minor?: number }
}
export interface ResellerApplyResult {
  reseller?: ResellerMe
  /** Present when a sign-up fee must be paid: continue on the order pay flow. */
  order_no?: string | null
  order?: { order_no: string; id?: string } | null
}
export interface ResellerProduct {
  product_id: string
  title: string
  cover: string | null
  currency: string
  base_price_minor: number
  markup_kind: MarkupKind
  markup_value: number
  /** true when a per-product override exists (false = reseller default) */
  custom: boolean
  price_minor: number
  hidden: boolean
}
export interface ResellerOrder {
  order_no: string
  title: string
  qty: number
  amount_minor: number
  markup_minor: number
  currency: string
  status: OrderStatus
  created_at: string
  buyer: string | null
}
export interface ResellerStats {
  today_orders: number
  today_earnings_minor: number
  week_orders: number
  week_earnings_minor: number
  currency?: string
}
export interface ResellerDomain {
  id: string
  hostname: string
  status: 'pending' | 'active' | 'failed' | 'deleted'
  ssl_status: string | null
  verification: {
    ownership?: { type?: string; name?: string; value?: string }
    ownership_http?: { http_url?: string; http_body?: string }
    ssl_records?: { txt_name?: string; txt_value?: string; http_url?: string; http_body?: string }[]
  }
  created_at: string
  verified_at: string | null
}
export interface ResellerDomains {
  enabled: boolean
  fallback_origin: string
  max_domains: number
  items: ResellerDomain[]
}
export type PayoutStatus = 'requested' | 'paid' | 'rejected'
export interface ResellerPayout {
  id: string
  amount_minor: number
  currency: string
  status: PayoutStatus
  note: string | null
  created_at: string
  decided_at: string | null
}
export type PowScope = 'instant' | 'register' | 'lookup' | 'login'
export interface PowChallenge {
  challenge: string
  bits: number
  /** Unix timestamp (seconds). */
  expires_at: number
}
export interface PowSolution {
  challenge: string
  nonce: string
}
export interface InstantCredentials {
  login: string
  password: string
}
export interface InstantResult {
  user: User
  credentials: InstantCredentials
}
export interface ArticleSummary {
  slug: string
  title: string
  category: string
  published_at: string | null
  updated_at: string | null
  views: number
  pinned: boolean
}
export interface Article {
  slug: string
  title: string
  category: string
  kind: 'article' | 'page'
  body_html: string
  published_at: string | null
  updated_at: string | null
  views: number
}

export type AgentStatusValue = 'online' | 'away' | 'on_hold' | 'offline'
export interface AgentStatus {
  status: AgentStatusValue
  message: string | null
  updated_at: string
}

export interface Paginated<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

export interface Category {
  key: string
  name: string
}

export type SendMessageType = 'text' | 'image' | 'voice' | 'file' | 'product_card'
export interface SendMessageBody {
  client_msg_id: string
  type: SendMessageType
  text?: string
  attachment_id?: string
  /** v4 (staff only): type=product_card; the server builds the payload. */
  product_id?: string
  /** v11 W2: reply to this message id (same conversation). */
  reply_to?: number
}

export interface MessageQuery {
  after_id?: number
  before_id?: number
  limit?: number
}

// ---- admin ----
export interface AdminStats {
  orders_today: number
  revenue_today: string
  pending_payment: number
  awaiting_manual: number
  open_conversations: number
  low_stock: ProductSummary[]
}
export interface OrderBuyer {
  id: string
  email: string
  display_name: string
}
export type AdminOrder = Order & { buyer: OrderBuyer }
export interface OrderEvent {
  from: OrderStatus | null
  to: OrderStatus
  actor: string
  reason: string | null
  at: string
}
export type AdminOrderDetail = AdminOrder & {
  events: OrderEvent[]
  /**
   * NOT in the contract (optional extension): per-unit floor price of the linked product so the
   * price-change dialog can show the floor. When absent the UI looks it up via /admin/products.
   */
  floor_price?: string | null
}
export type CardStatus = 'available' | 'reserved' | 'sold' | 'void'
export interface CardRow {
  id: string
  masked: string
  status: CardStatus
  order_id: string | null
  created_at: string
}
export interface CardImportResult {
  imported: number
  duplicates: number
  rejected: number
}
export interface QuoteInput {
  title: string
  amount: string
  qty: number
  note?: string
  product_id?: string
  /** Required by the server when the product has active variants. */
  variant_id?: string
  expires_in_minutes: number
}
export interface AgentInfo {
  id: string
  display_name: string
  status: AgentStatusValue
}
/** A canned CS reply. `shared` ones belong to the tenant, the rest to the signed-in agent. */
export interface ReplyTemplate {
  id: string
  title: string
  body: string
  sort: number
  shared: boolean
}
export interface ReplyTemplateInput {
  title: string
  body: string
  shared?: boolean
}
export interface LedgerRow {
  txn_id: string
  at: string
  account: string
  amount: string
  currency: string
  memo: string | null
  order_id: string | null
}
export interface AuditRow {
  at: string
  actor: string
  action: string
  target: string
  ip: string | null
  detail: unknown
}
export type ConversationFilter = 'all' | 'mine' | 'unassigned'

// ---- SSE ----
export interface StreamMessageEvent {
  conversation_id: string
  message: Message
}

/** Private server-generated EPay presentation, never a payment-success assertion. */
export interface EpayPaymentView {
  redirect_host: string | null
  attempt_id: string
  status: string
  attempt_status: string
  amount: string
  currency: string
  expires_at: string | null
  expired: boolean
  kind: 'qr' | 'redirect' | null
  redirect_url: string | null
  qr_svg: string | null
}

// review-v10: EPay gateway routing + test payments (admin)
export type GatewayRoutingMode = 'sticky' | 'priority'
export interface PaymentGatewayAdmin {
  id: string
  label: string
  gateway: string
  merchant_id: string
  mode: string
  active: boolean
  enabled: boolean
  priority: number
  in_use: boolean
  last_ok_at: string | null
  last_fail_at: string | null
  last_fail_reason: string | null
  created_at: string
}
export interface PaymentGatewayList {
  routing_mode: GatewayRoutingMode
  /** ids in the order the next payment will try them */
  candidates: string[]
  items: PaymentGatewayAdmin[]
}
export type TestPaymentStatus = 'pending' | 'paid' | 'expired' | 'failed'
export interface TestPaymentView {
  id: string
  gateway_id: string
  gateway_label: string | null
  out_trade_no: string
  amount: string
  currency: string
  status: TestPaymentStatus
  qr_content: string | null
  qr_svg: string | null
  redirect_url: string | null
  reason: string | null
  message: string | null
  seconds_left: number
  expires_at: string | null
  created_at: string
  paid_at: string | null
  trade_no: string | null
}
