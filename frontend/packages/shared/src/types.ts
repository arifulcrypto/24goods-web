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
}
export type ProductStatus = 'active' | 'draft'
export interface Product extends ProductSummary {
  description: string
  images: string[]
  status: ProductStatus
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
  kind: 'image' | 'voice'
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

export type SenderRole = 'buyer' | 'agent' | 'system'
export interface Message {
  id: number
  conversation_id: string
  client_msg_id: string | null
  sender: { id: string | null; role: SenderRole; display_name: string }
  type: MessageType
  text: string | null
  attachment: Attachment | null
  payload: ProductCardPayload | QuoteCardPayload | OrderCardPayload | SystemPayload | null
  created_at: string
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

export type SendMessageType = 'text' | 'image' | 'voice'
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
