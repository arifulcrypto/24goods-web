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
  /** v3 (assumed): login handle `u-xxxxxxx` of instant accounts; email may be empty for them. */
  login?: string | null
}

export type DeliveryMode = 'auto_card' | 'manual'

export interface ProductSummary {
  id: string
  title: string
  cover: string | null
  price: string
  currency: string
  delivery_mode: DeliveryMode
  /** stock only for auto_card */
  stock: number | null
  category: string
  sold: number
  /** v3: resolved after-sales window in hours (site default applied); 0 = no after-sales. */
  after_sales_hours?: number | null
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
}
export type I18nText = Record<Locale, string>
export interface AdminProduct extends Omit<Product, 'title' | 'description'> {
  title_i18n: I18nText
  description_i18n: I18nText
  floor_price: string
  created_at: string
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
  sender: { id: string | null; role: SenderRole; display_name: string }
  type: MessageType
  text: string | null
  attachment: Attachment | null
  payload: ProductCardPayload | QuoteCardPayload | OrderCardPayload | SystemPayload | FilePayload | null
  created_at: string
}
/** v3: `type=file` message payload (text/plain attachment). */
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
  buyer: { id: string; display_name: string }
  product: ProductCardPayload | null
  status: 'open' | 'closed'
  assigned_agent: { id: string; display_name: string } | null
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
  delivery_info?: DeliveryInfo | null
  source?: string | null
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
}
export interface SiteInfo {
  brand: string
  announcement: Announcement | null
  after_sales_default_hours: number
  support_hours: string | null
  pages: { slug: string; title: string }[]
}
export type PowScope = 'instant' | 'register' | 'lookup' | 'login'
export interface PowChallenge {
  challenge: string
  bits: number
  expires_at: string
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

export type SendMessageType = 'text' | 'image' | 'voice' | 'file'
export interface SendMessageBody {
  client_msg_id: string
  type: SendMessageType
  text?: string
  attachment_id?: string
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
  expires_in_minutes: number
}
export interface AgentInfo {
  id: string
  display_name: string
  status: AgentStatusValue
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
