"""Policy pages (EN source + zh-CN): Terms, Privacy, After-sales, Platform rules. Effective 2026-11-09.

Positioning (owner decision 2026-11-09): Kesiv is a venue/intermediary — it provides listing,
matching, communication, order-management and payment-collection services; the Seller of a
listing is responsible for the product. Governing law: Singapore. Pages show only the brand, the
governing law and the support address; no entity details, hosting locations, vendors or technical
measures. Clauses consumer law does not let a business exclude are preserved ("to the extent
permitted by law"): an unenforceable clause protects nobody.

Changing this text on a live site: edit the page in Admin -> Articles (pages are seeded once and
never overwritten), and bump EFFECTIVE_* so returning buyers can see what changed.
"""

from __future__ import annotations

import re

_CJK = re.compile(r"[　-鿿＀-￯]")
_BLOCK = re.compile(r"(\d+(\.\d+)? |- |#|_|---|\*\*Kesiv)")


def _flow(text: str) -> str:
    """Join soft-wrapped source lines into one line per paragraph / list item.

    Markdown turns a newline inside a paragraph into a space, which shows up as a stray gap
    between Chinese characters; English keeps its single space.
    """
    out: list[str] = []
    for line in text.splitlines():
        cont = line.strip()
        starts_block = not cont or _BLOCK.match(cont) or re.match(r"\d+\. ", cont)
        if out and out[-1] and not starts_block:
            prev = out[-1]
            a, b = prev[-1:], cont[:1]
            words = a.isascii() and a.isalnum() and b.isascii() and b.isalnum()
            sep = " " if words or not _CJK.search(prev + cont) else ""
            out[-1] = prev + sep + cont
        else:
            out.append(line)
    return "\n".join(out) + "\n"


SUPPORT_EMAIL = "support@kesiv.com"
EFFECTIVE_EN = "Effective date: 9 November 2026"
EFFECTIVE_ZH = "生效日期：2026 年 11 月 9 日"
FOOTER_EN = f"---\n\n**Kesiv** · Governing law: Singapore · Contact: {SUPPORT_EMAIL}"
FOOTER_ZH = f"---\n\n**Kesiv 客思维** · 适用法律：新加坡法律 · 联系邮箱：{SUPPORT_EMAIL}"

TERMS_EN = f"""_{EFFECTIVE_EN}_

These Terms of Service ("Terms") govern your use of Kesiv (the "Platform"), including its website
and storefronts. By creating an account, placing an order or otherwise using the Platform you agree
to these Terms and to the [Platform rules](/a/rules), the [After-sales policy](/a/after-sales) and
the [Privacy policy](/a/privacy), which form part of them. If you do not agree, do not use the
Platform.

## 1. What the Platform does

1.1 Kesiv is an online venue that connects buyers with sellers of digital goods and services
("Sellers"). We provide listing, matching, communication, order-management and payment-collection
services. We are not the maker, issuer or provider of the products listed.

1.2 Each sale is a contract between the buyer and the Seller of that listing. Unless a listing
expressly states that Kesiv is its Seller, we are not a party to that contract and **we assume no
joint or several liability for the quality, safety, legality or fitness of products sold by
Sellers**. Where Kesiv acts as Seller, the Seller provisions of these Terms apply to it in that
capacity.

1.3 Product names, logos and trademarks belong to their owners. Unless stated, neither we nor the
Sellers are affiliated with or endorsed by them.

## 2. Eligibility

2.1 You must be at least 18 years old (or the age of majority where you live) and able to enter
into a binding contract.

2.2 The Platform is not offered to residents of Mainland China. You may not use it where doing so
is unlawful or if you are subject to applicable sanctions. We may limit access in any region.

## 3. Accounts

3.1 Keep your sign-in details and any order access codes confidential. You are responsible for
activity under your account, including an account created for you at checkout.

3.2 Information you give us must be accurate. You may not use multiple accounts to abuse
promotions or evade restrictions.

3.3 We may refuse service, suspend or close accounts, cancel orders or withhold delivery where we
reasonably suspect fraud, abuse, a breach of these Terms or a legal requirement.

## 4. Orders, prices and payment

4.1 The price, including any payment-method surcharge, is shown at checkout. A price agreed in chat
applies only once it appears on the order. An order is accepted when its payment is confirmed;
unpaid orders expire after the time shown.

4.2 Payments are handled by third-party payment service providers offered at checkout, such as
card processors, PayPal, Stripe, local e-wallets and digital-asset payment services. Their terms
also apply to your payment. We do not store full payment card details.

4.3 Kesiv collects payments on behalf of Sellers. Payment to Kesiv is payment to the Seller.

4.4 You must pay the exact amount, in the currency and on the network shown. Payments that are
late, short, sent to a wrong address or network, or made in an unsupported asset may not be
credited or recoverable, and the related fees are borne by the sender.

4.5 If a listing shows an obvious pricing or description error, the order may be cancelled and
refunded.

## 5. Delivery

5.1 Delivery is digital and appears on your order page. Instant listings are delivered
automatically; others are delivered by the Seller within the time stated on the listing.

5.2 By paying you request immediate delivery and acknowledge that digital content cannot be
returned once delivered. To the extent permitted by law, you lose any right to cancel or withdraw
once delivery has been made.

5.3 A delivery shown on your order page counts as received. Check it promptly and keep it private.

## 6. After-sales, refunds and disputes

6.1 **Virtual goods cannot be returned or exchanged for a change of mind.** Remedies for items that
are not delivered or not as described are provided under the
[After-sales policy](/a/after-sales), which is the procedure you must use first.

6.2 Refunds are made to the original payment method only.

6.3 Contact us before opening a payment dispute or chargeback. If you open one without first using
after-sales, or one that turns out to be unfounded, we may suspend your account, revoke deliveries
where possible and recover the amounts and fees involved.

## 7. Sellers

Sellers must have the right to sell what they list, describe it accurately, deliver within the
stated time, honour the After-sales policy and comply with the law and the Platform rules. Sellers
are solely responsible for their products and for any taxes on their sales. Additional seller terms
may apply.

## 8. Acceptable use

You may not use the Platform for anything unlawful, fraudulent or harmful, including money
laundering or dealing in stolen goods; access it by scraping, crawling or other automated means;
interfere with its security or reverse engineer it; use its content to train machine-learning
models; arrange payment outside the Platform; or harass others.

## 9. Third-party platforms

Many products are used on platforms run by third parties (games, app stores, streaming and other
services). Their rules, region limits and decisions, including account restrictions, are outside
our and the Seller's control and are not covered unless caused by a defect present at delivery.

## 10. Intellectual property

The Platform and its content belong to us or our licensors. You may use them only to buy and use
products for your own purposes. We may use any feedback you give without obligation.

## 11. Disclaimers and limitation of liability

11.1 The Platform is provided "as is" and "as available". Listing information comes from Sellers.
We do not guarantee uninterrupted service or the conduct of any user.

11.2 To the extent permitted by law: (a) we are not liable for indirect or consequential loss, or
for loss of profit, data, business or goodwill; and (b) our total liability for any claim relating
to an order is limited to the amount you paid for that order, and for any other claim to SGD 100.

11.3 Nothing in these Terms limits liability that cannot be limited by law, including for fraud or
for death or personal injury caused by negligence.

## 12. Indemnity

You will indemnify Kesiv and the Sellers against claims, losses and costs arising from your breach
of these Terms or misuse of the Platform or products.

## 13. Records and notices

You agree to contract and receive notices electronically. Our order records, delivery records and
chat history are evidence of the transaction, and chat messages may be used to resolve an order.

## 14. Changes

We may change the Platform or these Terms. The version on this page applies from its effective
date; continuing to use the Platform after that means you accept it. Orders already placed remain
under the Terms in force when they were placed.

## 15. Governing law and disputes

15.1 These Terms and any dispute arising from them are governed by the laws of the Republic of
Singapore.

15.2 Please contact us first; we will try to resolve the matter within 30 days. Failing that, the
courts of Singapore have exclusive jurisdiction, though we may seek urgent relief in any court. To
the extent permitted by law, claims may be brought only individually, not as a class or
representative action.

15.3 If any provision is unenforceable, the rest remains in effect. Not enforcing a right is not a
waiver. We may transfer our rights and obligations under these Terms. If a translation differs from
the English version, the English version prevails.

{FOOTER_EN}
"""

TERMS_ZH = f"""_{EFFECTIVE_ZH}_

本服务条款（“本条款”）适用于您对 Kesiv（“本平台”，包括其网站及各店铺）的使用。您注册账号、下单或以其他方式
使用本平台，即表示同意本条款以及作为本条款组成部分的[平台规则](/a/rules)、[售后政策](/a/after-sales)
和[隐私政策](/a/privacy)。如不同意，请勿使用本平台。

## 1. 平台的角色

1.1 Kesiv 是连接买家与数字商品及服务卖家（“卖家”）的线上交易场所，提供信息发布、撮合、沟通、订单管理及代收款
等技术服务。我们不是所售商品的制造商、发行方或服务提供方。

1.2 每笔交易是买家与该商品卖家之间的合同。除商品页明确标明由 Kesiv 销售外，我们不是该合同的当事方，
**对第三方卖家所售商品的质量、安全、合法性或适用性不承担连带责任**。Kesiv 作为卖家销售时，本条款中关于卖家的规定
适用于 Kesiv。

1.3 商品名称、标识及商标归其各自权利人所有。除另有说明外，我们及卖家与其不存在关联或授权关系。

## 2. 使用资格

2.1 您须年满 18 周岁（或您所在地的法定成年年龄），并具备订立合同的能力。

2.2 本平台不向中国大陆居民提供服务。在法律禁止使用或您受适用制裁限制的情况下，您不得使用本平台。我们可在任何地区
限制访问。

## 3. 账号

3.1 请妥善保管您的登录信息及订单提取码。您须对账号下的一切活动负责，包括下单时为您创建的账号。

3.2 您提供的信息须真实准确。不得使用多个账号滥用优惠或规避限制。

3.3 如我们合理怀疑存在欺诈、滥用、违反本条款或法律要求的情形，可拒绝服务、暂停或关闭账号、取消订单或暂停发货。

## 4. 订单、价格与付款

4.1 价格（含所选支付方式的手续费）以结算页显示为准。在对话中议定的价格须显示在订单上方为有效。订单在付款确认后
成立；未在显示时限内付款的订单将失效。

4.2 付款由结算页提供的第三方支付服务商处理，例如银行卡收单机构、PayPal、Stripe、本地电子钱包及数字资产支付服务。
您的付款同时受其条款约束。我们不保存完整的银行卡信息。

4.3 Kesiv 代卖家收取货款。向 Kesiv 付款即视为向卖家付款。

4.4 您须按显示的币种、网络支付准确金额。逾期、少付、转错地址或网络、或使用不支持的资产付款，可能无法入账或追回，
相关费用由付款方承担。

4.5 如商品页存在明显的价格或描述错误，相关订单可被取消并退款。

## 5. 发货

5.1 发货以数字方式进行，内容显示在您的订单页。自动发货商品由系统即时发货；其他商品由卖家在商品页注明的时间内发货。

5.2 您付款即表示要求立即发货，并确认数字内容一经交付无法退回。在法律允许的范围内，发货后您不再享有取消或撤回的权利。

5.3 显示在订单页的发货内容即视为已收到。请及时查验并妥善保管。

## 6. 售后、退款与争议

6.1 **虚拟商品不支持无理由退换。** 对未发货或与描述不符的商品，依照[售后政策](/a/after-sales)处理，
您须首先通过售后渠道解决。

6.2 退款仅退回原支付方式。

6.3 发起支付争议或拒付前请先联系我们。如未先申请售后即发起拒付，或拒付经查不成立，我们可暂停您的账号，
在可能的情况下撤回已发货内容，并追偿相关金额及费用。

## 7. 卖家

卖家须对所售商品拥有合法权利，如实描述，按时发货，遵守售后政策、法律及平台规则。卖家对其商品及销售所涉税费
独立承担责任。卖家可能另需遵守其他卖家条款。

## 8. 合理使用

不得利用本平台从事违法、欺诈或有害活动（包括洗钱或买卖赃物）；不得以爬虫、抓取或其他自动化方式访问；
不得干扰平台安全或进行逆向工程；不得将平台内容用于训练机器学习模型；不得引导站外付款；不得骚扰他人。

## 9. 第三方平台

许多商品需在第三方平台（游戏、应用商店、流媒体等服务）上使用。其规则、地区限制及决定（包括账号限制）不受我们及卖家
控制，除因发货时已存在的缺陷所致外，不在保障范围内。

## 10. 知识产权

本平台及其内容归我们或我们的许可方所有。您仅可为自用目的购买及使用商品而使用。我们可无偿使用您提供的任何反馈。

## 11. 免责与责任限制

11.1 本平台按“现状”及“现有”基础提供。商品信息由卖家提供。我们不保证服务不中断，也不保证任何用户的行为。

11.2 在法律允许的范围内：（a）我们不对间接或附带损失，或利润、数据、业务、商誉损失承担责任；（b）对任一订单相关的
索赔，我们的累计责任以您为该订单支付的金额为限，其他索赔以 100 新加坡元为限。

11.3 本条款不限制依法不得限制的责任，包括欺诈责任以及因过失导致人身伤亡的责任。

## 12. 赔偿

因您违反本条款或不当使用本平台或商品而引起的索赔、损失及费用，由您向 Kesiv 及卖家作出赔偿。

## 13. 记录与通知

您同意以电子方式订立合同及接收通知。我们的订单记录、发货记录及聊天记录可作为交易证据，聊天消息可用于处理订单。

## 14. 变更

我们可变更本平台或本条款。本页所示版本自生效日期起适用，您继续使用即表示接受。已下单的订单仍适用下单时有效的条款。

## 15. 适用法律与争议解决

15.1 本条款及由此产生的任何争议均适用新加坡共和国法律。

15.2 请先联系我们，我们将在 30 日内尽力解决。协商不成的，由新加坡法院专属管辖，但我们可向任何法院申请紧急救济。
在法律允许的范围内，索赔仅可以个人名义提出，不得以集体或代表诉讼方式提出。

15.3 任何条款无法执行的，不影响其余条款的效力。未行使权利不构成弃权。我们可转让本条款下的权利和义务。
译文与英文版本不一致的，以英文版本为准。

{FOOTER_ZH}
"""

PRIVACY_EN = f"""_{EFFECTIVE_EN}_

This policy explains how Kesiv ("we") collects, uses and shares personal data when you use the
Platform.

## 1. Data we collect

- **Account:** email address, password (stored in protected form), display name and language. If
  you sign in with a third-party account, the profile details that provider shares with us, such
  as your name, email address and account identifier.
- **Orders:** products, amounts, payment method and status, transaction references, details you
  provide for delivery (for example a game ID) and delivery records.
- **Communications:** chat messages, voice messages, images and files you send, and after-sales
  requests.
- **Device and usage:** browser and device information, approximate location, log data, and cookies
  or similar technologies needed for sign-in, security and your preferences.

Payment card details are collected by the payment provider, not by us.

## 2. How we use it

To run the Platform and your account; process orders, payments and deliveries; provide customer
service and after-sales; send service messages such as order updates and renewal reminders;
prevent fraud and abuse and keep the Platform secure; meet legal obligations; and improve the
Platform. We send marketing only where you have agreed or the law allows, and you can opt out at
any time.

## 3. Sharing

- **Sellers:** what they need to fulfil and support your order.
- **Service providers working for us:** hosting, infrastructure and security; payment providers;
  email and messaging; the sign-in providers you choose; customer-support tools. They may use the
  data only to provide their services to us.
- **Legal and safety:** where required by law, or to protect rights, prevent fraud or handle
  payment disputes.
- **Business transfers:** to a successor in a merger, acquisition or sale of assets.

We do not sell personal data.

## 4. International transfers

Your data may be processed outside your country. Where we transfer it, we take steps to keep it
protected to the standard required by applicable law.

## 5. Retention

We keep personal data only as long as needed for the purposes above. Order and payment records are
kept for the periods the law requires; afterwards data is deleted or anonymised.

## 6. Security

We use reasonable technical and organisational measures and limit access to people who need it.
No system is completely secure, so please protect your own sign-in details.

## 7. Your rights

Depending on where you live, you may ask to access, correct, delete or export your data, withdraw
consent, or object to some processing. You can change your details and delete your account in
**Me → Settings**; for anything else, email us. We may need to verify your identity and may keep
data that we are required or entitled to keep.

## 8. Children

The Platform is not intended for anyone under 18, and we do not knowingly collect their data.

## 9. Changes

We may update this policy. This page always shows the current version and its effective date.

## 10. Contact

For privacy and data-protection requests, email {SUPPORT_EMAIL}.

{FOOTER_EN}
"""

PRIVACY_ZH = f"""_{EFFECTIVE_ZH}_

本政策说明 Kesiv（“我们”）在您使用本平台时如何收集、使用和共享个人信息。

## 1. 我们收集的信息

- **账号信息：** 邮箱地址、密码（以受保护形式存储）、昵称及语言。如您使用第三方账号登录，还包括该服务商向我们
  提供的资料，例如姓名、邮箱及账号标识。
- **订单信息：** 商品、金额、支付方式及状态、交易凭证编号、您为发货提供的信息（例如游戏 ID）及发货记录。
- **沟通信息：** 您发送的聊天消息、语音、图片及文件，以及售后申请。
- **设备与使用信息：** 浏览器及设备信息、大致位置、日志，以及登录、安全和偏好设置所必需的 Cookie 或类似技术。

银行卡信息由支付服务商收集，我们不收集。

## 2. 使用目的

用于运营本平台及您的账号；处理订单、付款和发货；提供客服及售后；发送订单动态、续费提醒等服务通知；防范欺诈与滥用、
保障平台安全；履行法律义务；以及改进本平台。我们仅在您同意或法律允许时发送营销信息，您可随时退订。

## 3. 信息共享

- **卖家：** 履行及支持您的订单所需的信息。
- **为我们提供服务的服务商：** 托管、基础设施及安全服务商；支付服务商；邮件及消息服务商；您选择的登录服务商；
  客服工具。其仅可为向我们提供服务而使用相关信息。
- **法律与安全：** 依法要求时，或为保护权利、防范欺诈、处理支付争议。
- **业务转让：** 在合并、收购或资产出售时转让给承继方。

我们不出售个人信息。

## 4. 跨境传输

您的信息可能在您所在国家/地区以外处理。进行传输时，我们会采取措施使其达到适用法律要求的保护水平。

## 5. 保存期限

我们仅在实现上述目的所需期间内保存个人信息。订单及付款记录按法律要求的期限保存，期满后删除或匿名化。

## 6. 安全

我们采取合理的技术和管理措施，并将访问权限限于确有需要的人员。任何系统都无法做到绝对安全，请妥善保管您的登录信息。

## 7. 您的权利

根据您所在地的法律，您可能有权访问、更正、删除或导出您的信息，撤回同意，或反对某些处理。您可在
**我的 → 设置** 中修改资料及删除账号；其他请求请发送邮件联系我们。我们可能需要核实您的身份，并可能保留依法须保留
或有权保留的信息。

## 8. 未成年人

本平台不面向 18 周岁以下人士，我们不会故意收集其信息。

## 9. 政策变更

我们可能更新本政策。本页始终显示现行版本及其生效日期。

## 10. 联系我们

如有隐私或个人信息相关请求，请发送邮件至 {SUPPORT_EMAIL}。

{FOOTER_ZH}
"""

AFTER_SALES_EN = f"""_{EFFECTIVE_EN}_

This policy explains what happens if an order is not delivered or not as described. It is part of
the [Terms of service](/a/terms).

## 1. The basics

**Virtual goods cannot be returned or exchanged for a change of mind.** After-sales covers problems
with the item itself, reported within the after-sales period through the order page.

## 2. After-sales period

- Each listing shows its after-sales period, which starts at delivery. The exact deadline is shown
  on your order and prevails.
- Unless the listing states otherwise, the period is **one calendar month**. Some products, such as
  accounts, have shorter periods (for example 24 hours).
- For subscriptions and other time-based services, cover lasts for the paid service period shown on
  the order.
- Confirming receipt, or automatic confirmation, does not shorten the period.

## 3. What is covered

- The item was not delivered within the stated time.
- A code or key was invalid or already used at the time of delivery.
- The item delivered is not the one ordered or does not match the listing.
- Account details do not work at first sign-in, reported within the period.
- A subscription or service stops working during the paid period through no fault of yours.

## 4. What is not covered

- Change of mind, buying by mistake, or choosing the wrong region, platform or version when the
  listing stated it.
- Items that have been redeemed, used, shared or changed after delivery.
- Problems caused by you, such as not following the delivery instructions or breaking a third-party
  platform's rules.
- Restrictions, bans or policy changes by a third-party platform not caused by a defect present at
  delivery.
- Requests made after the period ends, or without enough evidence.

## 5. How to request

1. Open the order and tap **Request after-sales** before the deadline.
2. Choose a reason, describe the problem and attach screenshots or a screen recording that shows
   the error, with the time visible where possible.
3. Leave the item as delivered and answer follow-up questions. A request may be closed if you do
   not reply within 72 hours.

Only one request can be open per order at a time.

## 6. Outcomes

We usually respond within one business day. Depending on the evidence, the outcome is one of:

- **Replacement or redelivery** of the same or an equivalent item. This is the first remedy where
  available.
- **Extension** of the service period, for subscriptions and services.
- **Refund**, full or partial, to the original payment method. For subscriptions, refunds cover the
  unused part of the paid period.
- **Rejection**, with the reason.

Refund timing depends on the payment provider. Digital-asset refunds go to an address you provide on
the same network, less network fees. Decisions are based on the evidence provided and on our order,
delivery and chat records.

## 7. Abuse and chargebacks

False or repeated unfounded claims, threats, or a chargeback opened without first using after-sales
may lead to account suspension, revocation of deliveries where possible and recovery of fees.

## 8. Your legal rights

Nothing in this policy affects rights you have under applicable law that cannot be excluded.

{FOOTER_EN}
"""

AFTER_SALES_ZH = f"""_{EFFECTIVE_ZH}_

本政策说明订单未发货或与描述不符时的处理方式，是[服务条款](/a/terms)的组成部分。

## 1. 基本原则

**虚拟商品不支持无理由退换。** 售后仅针对商品本身的问题，须在售后期限内通过订单页提出。

## 2. 售后期限

- 每个商品均标明售后期限，自发货时起计算。具体截止时间以订单页显示为准。
- 除商品页另有说明外，售后期限为 **一个自然月**。部分商品（例如账号）期限较短（例如 24 小时）。
- 订阅及其他按时长计费的服务，在订单所示的已付费服务期内享有保障。
- 确认收货或系统自动确认收货，不会缩短售后期限。

## 3. 保障范围

- 未在注明时间内发货。
- 卡密或激活码在发货时即无效或已被使用。
- 所发商品与所购商品不符或与描述不符。
- 账号信息在首次登录时无法使用，且在期限内提出。
- 订阅或服务在已付费期间内非因您的原因停止工作。

## 4. 不在保障范围

- 无理由退换、误购，或商品页已注明地区、平台、版本而您选择错误。
- 发货后已兑换、使用、分享或修改的商品。
- 因您自身原因造成的问题，例如未按发货说明操作或违反第三方平台规则。
- 第三方平台的限制、封禁或政策变化，且并非由发货时已存在的缺陷导致。
- 超过期限或证据不足的申请。

## 5. 如何申请

1. 在截止时间前打开订单，点击 **申请售后**。
2. 选择原因、描述问题，并上传能显示错误的截图或录屏，尽量包含时间。
3. 保持商品为发货时的状态，并及时回复客服的询问。如 72 小时内未回复，申请可能被关闭。

每个订单同一时间只能有一个进行中的申请。

## 6. 处理结果

我们通常在一个工作日内回复。根据证据，处理结果为以下之一：

- **换货或补发** 相同或同等商品，有货时优先采用。
- **延长服务期**，适用于订阅及服务类商品。
- **退款**（全额或部分），退回原支付方式。订阅类商品按已付费期间中未使用的部分退款。
- **驳回**，并说明原因。

退款到账时间取决于支付服务商。数字资产退款将转至您提供的同一网络地址，并扣除网络手续费。处理决定以您提供的证据及
我们的订单、发货和聊天记录为依据。

## 7. 滥用与拒付

虚假或多次无理申请、威胁，或未先申请售后即发起拒付，可能导致账号被暂停、在可能的情况下撤回已发货内容并追偿费用。

## 8. 法定权利

本政策不影响您依适用法律享有且不可排除的权利。

{FOOTER_ZH}
"""

RULES_EN = f"""_{EFFECTIVE_EN}_

These rules keep trading on Kesiv safe and fair. They are part of the
[Terms of service](/a/terms).

## 1. Everyone

- Follow the Terms of service and the law.
- Keep orders, payments and communication on the Platform. Deals or payments made elsewhere are not
  protected and may lead to suspension.
- Be respectful: no harassment, threats, spam, or sharing other people's personal data.
- Do not use multiple accounts to abuse promotions or evade restrictions.

## 2. Buyers

- Before paying, read the listing: region, platform, delivery time, format and after-sales period.
- Give accurate order details (for example game ID or server). Problems caused by wrong details are
  your responsibility.
- Check deliveries promptly and report problems through after-sales within the period.
- Keep codes and accounts private, and secure accounts as instructed in the delivery.

## 3. Sellers

- List only items you have the right to sell and that were obtained lawfully.
- Describe items accurately, including region, restrictions, delivery time and after-sales period.
- Deliver within the stated time and keep proof of delivery.
- Answer buyers and after-sales requests promptly and carry out the outcome.
- Do not change the price after an order is placed, ask for payment outside the Platform, or ask for
  more of the buyer's information than the order needs.

## 4. Prohibited

- Stolen, hacked or fraudulently obtained accounts, cards or codes; payment fraud; money laundering.
- Malware, hacking tools, illegal content, counterfeit goods, identity documents or personal data.
- Manipulating prices or promotions, or exploiting errors on the Platform.
- Scraping or other automated access.

## 5. Enforcement

Depending on how serious a breach is, we may warn, remove listings, cancel orders, hold funds while
we investigate, or suspend or close accounts. Decisions are based on our records. To appeal, contact
customer service.

## 6. Changes

We may update these rules. The version on this page applies.

{FOOTER_EN}
"""

RULES_ZH = f"""_{EFFECTIVE_ZH}_

本规则旨在保障 Kesiv 上的交易安全、公平，是[服务条款](/a/terms)的组成部分。

## 1. 所有用户

- 遵守服务条款及法律法规。
- 订单、付款和沟通请在本平台内完成。站外交易或付款不受保障，并可能导致账号被暂停。
- 文明交流：禁止骚扰、威胁、垃圾信息或泄露他人个人信息。
- 不得使用多个账号滥用优惠或规避限制。

## 2. 买家

- 付款前请阅读商品页：地区、平台、发货时间、发货格式及售后期限。
- 准确填写订单信息（例如游戏 ID 或区服）。因信息错误导致的问题由您自行承担。
- 及时查验发货内容，如有问题请在期限内通过售后处理。
- 妥善保管卡密和账号，并按发货说明做好账号安全设置。

## 3. 卖家

- 仅出售您有权出售且合法取得的商品。
- 如实描述商品，包括地区、限制、发货时间及售后期限。
- 在注明时间内发货并保留发货凭证。
- 及时回复买家及售后申请，并执行处理结果。
- 下单后不得涨价，不得要求站外付款，不得索取订单所需以外的买家信息。

## 4. 禁止事项

- 被盗、被黑或以欺诈方式获得的账号、卡片或卡密；支付欺诈；洗钱。
- 恶意软件、黑客工具、违法内容、假冒商品、身份证件或个人信息。
- 操纵价格或优惠，或利用平台错误牟利。
- 爬虫抓取或其他自动化访问。

## 5. 违规处理

视情节轻重，我们可给予警告、下架商品、取消订单、在调查期间冻结资金，或暂停、关闭账号。处理以我们的记录为依据。
如需申诉，请联系客服。

## 6. 规则变更

我们可能更新本规则，以本页所示版本为准。

{FOOTER_ZH}
"""


TERMS_EN, TERMS_ZH = _flow(TERMS_EN), _flow(TERMS_ZH)
PRIVACY_EN, PRIVACY_ZH = _flow(PRIVACY_EN), _flow(PRIVACY_ZH)
AFTER_SALES_EN, AFTER_SALES_ZH = _flow(AFTER_SALES_EN), _flow(AFTER_SALES_ZH)
RULES_EN, RULES_ZH = _flow(RULES_EN), _flow(RULES_ZH)
