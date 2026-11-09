"""Terms of Service and Privacy Policy (EN source + zh-CN), effective 2026-11-09.

Written for the operator and the sellers on the platform: digital delivery is final once made,
after-sales is the single remedy channel, chargebacks and payment errors are allocated to the
party that caused them, and logs/chat/delivery records are evidence. Clauses that consumer law
does not allow a business to contract out of are explicitly preserved ("to the extent permitted
by law"), because an unenforceable clause protects nobody. The privacy policy describes only what
the system actually does (see docs/architecture.md); keep it in step with the code.

Changing this text on a live site: edit the page in Admin -> Articles (pages are seeded once and
never overwritten), and bump EFFECTIVE_* so returning buyers can see what changed.
"""

from __future__ import annotations

import re

_CJK = re.compile(r"[\u3000-\u9fff\uff00-\uffef]")


def _flow(text: str) -> str:
    """Join soft-wrapped source lines into one line per paragraph / list item.

    Markdown turns a newline inside a paragraph into a space, which shows up as a stray gap
    between Chinese characters; English keeps its single space.
    """
    out: list[str] = []
    for line in text.splitlines():
        cont = line.strip()
        starts_block = not cont or cont.startswith(("- ", "#", "_")) or re.match(r"\d+\.\d+ ", cont)
        if out and out[-1] and not starts_block:
            prev = out[-1]
            sep = "" if _CJK.search(prev[-1:]) and _CJK.search(cont[:1]) else " "
            out[-1] = prev + sep + cont
        else:
            out.append(line)
    return "\n".join(out) + "\n"


EFFECTIVE_EN = "Effective date: 9 November 2026"
EFFECTIVE_ZH = "生效日期：2026 年 11 月 9 日"

TERMS_EN = f"""_{EFFECTIVE_EN}_

These Terms of Service ("Terms") govern your use of this website and any storefront operated on
it (the "Service"). The Service is run by Kesiv ("we", "us", the "Operator"). Goods are offered
by the Operator or by an independent merchant running a storefront on the platform (each a
"Seller"). By creating an account (including an instant account), placing an order or otherwise
using the Service, you ("you", the "Buyer") agree to these Terms, the
[Platform rules](/a/rules), the [After-sales policy](/a/after-sales) and the
[Privacy policy](/a/privacy). If you do not agree, do not use the Service.

## 1. Eligibility and availability

1.1 You must be at least 18 years old, or the age of legal majority where you live, and able to
enter into a binding contract.

1.2 **The Service is not offered to residents of Mainland China, and orders from Mainland China
are not accepted.** You may not use the Service where doing so is unlawful, and you may not
use it if you are subject to sanctions that prohibit dealing with you.

1.3 You confirm that the information you give us is accurate. We may refuse, cancel or refund
any order, and restrict any account, where we reasonably believe this section is not met.

## 2. Accounts

2.1 You are responsible for everything done through your account, including instant accounts
created at checkout. Keep your login, password and order access codes secret; we are not
responsible for losses caused by credentials you shared or failed to protect.

2.2 An instant account is protected only by the credentials shown to you once. If you lose them
and did not add an email address, we may be unable to restore access.

2.3 One person may not operate multiple accounts to evade limits, promotions, suspensions or
risk controls.

## 3. Products

3.1 Products are digital: card codes, account credentials, subscriptions, top-ups and services
delivered online. Each product page states what you receive, the delivery method, any
prerequisites (for example region, account type or information you must supply) and its
after-sales period. **Read the product page before you pay.**

3.2 Many products relate to third-party platforms. Neither the Operator nor the Seller is
affiliated with, endorsed by or responsible for those platforms, and third-party names and
marks belong to their owners. Your use of a third-party service is subject to that service's
own terms. We do not guarantee that a third party will continue to accept, support or not
restrict a code, account or subscription after it has been delivered in working order.

3.3 Images and descriptions are for guidance. A product is "as described" if it matches the
product page at the time you paid.

## 4. Prices and orders

4.1 Prices are set by our servers and shown before you pay, together with any payment-method fee.
A price agreed with customer service only applies once it appears on your order. Prices may
change at any time; the price on your order at the time of payment applies to that order.

4.2 If a price is obviously wrong (for example a typing or system error), we may cancel the order
and refund what you paid, even after confirmation.

4.3 An order becomes binding when payment for the exact amount shown is confirmed by our systems.
Unpaid orders expire automatically.

## 5. Payment

5.1 Payments are processed by third-party payment providers or on public blockchains. We never
see or store your bank card or wallet keys.

5.2 **Cryptocurrency payments must be sent in the exact amount, on the exact network, to the exact
address shown for that order, before it expires.** Blockchain transfers cannot be reversed. You
bear network fees. A transfer that is late, short, on the wrong network or to an old or
different address is not a valid payment; where we can identify and recover it we may, at our
discretion, apply it to the order or return it less network and handling costs, but we are not
liable for funds we cannot identify or recover. Any exchange rate shown is fixed for that
payment attempt only.

5.3 You must not tamper with, replay, alter the amount of or otherwise interfere with any payment
request or confirmation. Such attempts are fraud: the order will not be fulfilled, the account
may be closed and the evidence may be reported to the payment provider and the authorities.

## 6. Delivery

6.1 Automatic products are delivered on your order page immediately after payment is confirmed.
Manual products are delivered by the Seller within the time stated on the product page.

6.2 **By paying for a digital product you request immediate delivery, and you acknowledge that
once the product has been delivered (shown or made available to you) it cannot be returned,
and any statutory right to withdraw from the purchase ends to the extent the law allows.**

6.3 Delivery is complete when the content is made available on your order page or in your
order chat. Delivery records, including when content was first revealed, are kept as evidence.
You are responsible for storing delivered content safely.

## 7. After-sales, refunds and chargebacks

7.1 Your remedy for a product that is defective or not as described is set out in the
[After-sales policy](/a/after-sales): a request through your order within the product's
after-sales period, with the evidence asked for. The Seller may replace the product, refund it to the
original payment method, or reject the request with reasons. When the period ends without an open request, the order is
final.

7.2 There are no refunds for change of mind, for products that worked when delivered, for codes
or accounts that were used, shared, resold or altered after delivery, for problems caused by
you or a third-party platform's own decisions about your account, or where you supplied wrong
order information.

7.3 **Contact us before disputing a payment.** If you open a chargeback or payment dispute
without first using the after-sales process, or for an order that was delivered as described,
we may suspend your account, revoke or disable delivered goods where technically possible,
refuse future orders and recover the amount and any fees charged to us, and we will provide our
records to the payment provider.

7.4 Nothing in this section limits rights that the law where you live gives you and that cannot
be excluded by contract.

## 8. Acceptable use

You must not:

- use the Service or any product for fraud, money laundering, or any unlawful purpose;
- sell or supply stolen, hacked or unlawfully obtained goods or credentials;
- probe, scan, attack, overload or bypass the security, rate limits or verification of the
  Service;
- copy, scrape, crawl, mirror or frame the Service, or use automated tools to access it,
  except search engines that follow our robots.txt;
- reverse engineer, decompile or create derivative or competing storefronts from the Service's
  software, design or content;
- use any part of the Service to train, fine-tune or evaluate machine-learning models;
- harass customer service, impersonate others or send unlawful or abusive content.

We may remove content, cancel orders, suspend or close accounts and withhold unfulfilled orders
pending review where we reasonably suspect a breach of these Terms.

## 9. Records and evidence

We keep technical and security logs (including IP addresses and request details), order and
payment records, delivery records and chat history. You agree that these records, as kept by
our systems, may be used as evidence of what happened in any dispute with you, with a payment
provider or before an authority, and that chat messages are a valid way to make agreements about
an order.

## 10. Intellectual property

The Service, including its software, design, text, graphics and the Kesiv name, belongs to the
Operator or its licensors and is protected by law. You receive a limited, revocable, personal
right to use the Service for buying products for yourself. All other rights are reserved.

## 11. Sellers and storefronts

Where a product is sold by a merchant on its own storefront, that merchant is the Seller and is
responsible for the product, its description and its delivery; the Operator provides the
platform, payment handling and the after-sales process and may act on behalf of the Seller in
it. Sellers are additionally bound by their own agreement with the Operator.

## 12. Disclaimers

Except as expressly stated in these Terms, the Service is provided "as is" and "as available".
To the extent permitted by law, we make no other warranties, including about uninterrupted
availability, fitness for a particular purpose, or the continued acceptance of a product by a
third-party platform.

## 13. Limitation of liability

13.1 To the extent permitted by law, the total liability of the Operator and the Seller for any
claim relating to an order is limited to the amount you actually paid for that order, and
neither is liable for indirect or consequential loss, loss of profit, revenue, data, business or
goodwill, or for losses caused by third-party platforms, payment networks or blockchains.

13.2 Nothing in these Terms excludes liability that cannot be excluded by law, such as for fraud,
or for death or personal injury caused by negligence.

## 14. Indemnity

To the extent permitted by law, you will compensate the Operator and the Seller for losses,
fees (including payment-provider fees and reasonable legal costs) and claims arising from your
breach of these Terms, your unlawful use of the Service or a product, or an unjustified
chargeback.

## 15. Suspension and termination

You may stop using the Service and delete your account at any time in **My Account → Settings**.
We may suspend or terminate access at any time for a breach of these Terms, suspected fraud, a
legal requirement or to protect the Service, other users or a Seller. Sections that by their
nature should survive termination (including 5, 7, 9, 10 and 12–17) survive it.

## 16. Changes

We may change these Terms and the Service. The current version and its effective date are
always shown on this page. Changes apply to orders placed after they take effect; continuing to
use the Service after that date means you accept them.

## 17. Law and disputes

These Terms are governed by the laws of the place where the Operator is established, without
regard to conflict-of-law rules, and its courts have jurisdiction, except where the law of your
country of residence gives you a mandatory right to bring proceedings or to the protection of
its laws. Before starting any proceedings, please contact customer service so we can try to
resolve the matter; most issues are settled through the after-sales process. If a provision is
held unenforceable, the rest remains in effect. These Terms, together with the pages linked
above, are the entire agreement between you and us about the Service. If a translation differs
from the English version, the English version prevails to the extent permitted by law.

## 18. Contact

Contact us through **Customer service** in the store (My Account → Contact customer service) or
the chat on your order.
"""

TERMS_ZH = f"""_{EFFECTIVE_ZH}_

本服务条款（"本条款"）适用于您对本网站及其上运营的任何店铺（统称"本服务"）的使用。本服务由
Kesiv（"我们"或"运营方"）运营。商品由运营方或在本平台开设店铺的独立商家（统称"卖家"）提供。
当您注册账号（包括一键账号）、下单或以其他方式使用本服务时，即表示您（"您"或"买家"）同意本条款、
[平台规则](/a/rules)、[售后政策](/a/after-sales)及[隐私政策](/a/privacy)。如您不同意，请勿使用本服务。

## 1. 使用资格与服务范围

1.1 您须年满 18 周岁或已达到您所在地的法定成年年龄，并具备订立有约束力合同的能力。

1.2 **本服务不向中国大陆居民提供，亦不接受来自中国大陆的订单。** 在使用本服务违反当地法律的地区，
您不得使用本服务；如您受到禁止与您交易的制裁措施约束，亦不得使用本服务。

1.3 您确认所提供的信息真实准确。如我们合理认为不符合本条要求，可拒绝、取消或退款任何订单，并限制
相关账号。

## 2. 账号

2.1 您需对通过您账号进行的一切行为负责，包括结账时创建的一键账号。请妥善保管登录名、密码及订单
访问码；因您泄露或未妥善保管凭据造成的损失，我们不承担责任。

2.2 一键账号仅由一次性向您展示的凭据保护。如您遗失凭据且未绑定邮箱，我们可能无法为您恢复访问。

2.3 不得以一人多号的方式规避限额、优惠规则、封禁或风控措施。

## 3. 商品

3.1 本平台商品均为数字商品：卡密、账号凭据、订阅、充值及线上交付的服务。每个商品页均注明您将获得
的内容、发货方式、前提条件（例如地区、账号类型或需提供的信息）以及售后期限。**付款前请仔细阅读商品页。**

3.2 许多商品涉及第三方平台。运营方及卖家均与该等平台无关联、未获其认可，亦不对其负责；第三方名称
及商标归其各自权利人所有。您对第三方服务的使用受该服务自身条款约束。商品在交付时可正常使用后，我们
不保证第三方会持续接受、支持或不限制相关卡密、账号或订阅。

3.3 图片与描述仅供参考。商品与您付款时商品页所述一致，即视为"与描述相符"。

## 4. 价格与订单

4.1 价格由服务器确定，并在付款前连同支付方式手续费一并显示。与客服议定的价格仅在显示于订单上后
生效。价格可随时调整；订单以付款时订单上显示的价格为准。

4.2 如价格明显错误（例如录入或系统错误），即使订单已确认，我们仍可取消订单并退还您已支付的款项。

4.3 订单在我们的系统确认收到与订单所示金额完全一致的付款后生效。未支付订单将自动过期。

## 5. 支付

5.1 支付由第三方支付服务商处理或在公开区块链上完成。我们不会看到或存储您的银行卡或钱包私钥。

5.2 **加密货币付款须在订单过期前，按订单所示的准确金额、准确网络转账至准确地址。** 区块链转账不可撤销，
网络手续费由您承担。逾期、金额不足、网络错误或转至旧地址或其他地址的转账均不构成有效付款；如我们能够
识别并追回，可酌情将其用于该订单或在扣除网络及处理费用后退回，但对无法识别或追回的款项不承担责任。
页面显示的汇率仅对该次支付有效。

5.3 不得篡改、重放、修改金额或以其他方式干扰任何支付请求或支付确认。此类行为属于欺诈：订单将不予
履行，账号可能被关闭，相关证据可能提交给支付服务商及有关机关。

## 6. 发货

6.1 自动发货商品在付款确认后立即在订单页发货。人工发货商品由卖家在商品页所示时间内发货。

6.2 **您为数字商品付款，即表示您要求立即交付，并确认商品一经交付（向您显示或可供您获取）即不可退回，
在法律允许的范围内，任何法定撤销权随之终止。**

6.3 内容在您的订单页或订单聊天中可供获取时，即视为交付完成。交付记录（包括内容首次被查看的时间）
将作为证据保存。您应自行妥善保存已交付的内容。

## 7. 售后、退款与拒付

7.1 商品存在缺陷或与描述不符时，您的救济方式见[售后政策](/a/after-sales)：在商品售后期限内通过订单
提交申请并提供所需证据。卖家可更换商品、退款至原支付方式，
或说明理由后驳回申请。售后期限届满且无进行中的申请时，订单即为最终完成。

7.2 以下情形不予退款：主观不想要；交付时可正常使用的商品；交付后被使用、分享、转售或更改的卡密或
账号；因您自身原因或第三方平台对您账号的自主决定导致的问题；或您提供了错误的下单信息。

7.3 **申请支付争议前请先联系我们。** 如您未先通过售后流程即发起拒付或支付争议，或对已按描述交付的订单
发起争议，我们可暂停您的账号，在技术可行时撤销或停用已交付商品，拒绝您今后的订单，追偿相应款项及
我们被收取的费用，并将我们的记录提供给支付服务商。

7.4 本条不限制您所在地法律赋予您且不得以合同排除的权利。

## 8. 使用规范

您不得：

- 将本服务或任何商品用于欺诈、洗钱或任何违法目的；
- 销售或提供被盗、被入侵或以非法方式取得的商品或凭据；
- 探测、扫描、攻击、过载或绕过本服务的安全措施、频率限制或验证；
- 复制、抓取、爬取、镜像或嵌套本服务，或使用自动化工具访问本服务（遵守本站 robots.txt 的搜索引擎除外）；
- 对本服务的软件、设计或内容进行逆向工程、反编译，或据此制作衍生或竞争性店铺；
- 将本服务的任何部分用于训练、微调或评估机器学习模型；
- 骚扰客服、冒充他人或发送违法、辱骂性内容。

如我们合理怀疑存在违反本条款的行为，可删除内容、取消订单、暂停或关闭账号，并在审查期间暂缓履行
未完成的订单。

## 9. 记录与证据

我们保存技术与安全日志（包括 IP 地址及请求信息）、订单与支付记录、交付记录及聊天记录。您同意，在您与
我们之间、与支付服务商之间的任何争议中或在有关机关面前，由我们系统保存的上述记录可作为相关事实的证据；
聊天消息是就订单达成约定的有效方式。

## 10. 知识产权

本服务（包括其软件、设计、文字、图形及 Kesiv 名称）归运营方或其许可方所有并受法律保护。您仅获得为
自身购买商品而使用本服务的有限、可撤销的个人权利。其他一切权利均予保留。

## 11. 卖家与店铺

商品由商家在其自有店铺中销售时，该商家即为卖家，对商品、商品描述及发货负责；运营方提供平台、支付
处理及售后流程，并可在售后流程中代表卖家处理。卖家另受其与运营方之间协议的约束。

## 12. 免责声明

除本条款明确约定外，本服务按"现状"及"现有"基础提供。在法律允许的范围内，我们不作其他任何保证，
包括不保证服务不中断、适合特定用途或商品持续被第三方平台接受。

## 13. 责任限制

13.1 在法律允许的范围内，运营方及卖家就任一订单相关索赔承担的总责任，以您就该订单实际支付的金额为限；
对间接或附带损失，利润、收入、数据、业务或商誉损失，以及因第三方平台、支付网络或区块链造成的损失，
均不承担责任。

13.2 本条款不排除依法不得排除的责任，例如欺诈责任，或因过失造成人身伤亡的责任。

## 14. 赔偿

在法律允许的范围内，因您违反本条款、违法使用本服务或商品，或发起无正当理由的拒付而产生的损失、费用
（包括支付服务商收取的费用及合理的法律费用）及索赔，由您向运营方及卖家赔偿。

## 15. 暂停与终止

您可随时停止使用本服务，并在 **我的 → 设置** 中删除账号。因违反本条款、涉嫌欺诈、法律要求或为保护本服务、
其他用户或卖家，我们可随时暂停或终止您的访问。依其性质应在终止后继续有效的条款（包括第 5、7、9、10 条
及第 12–17 条）在终止后继续有效。

## 16. 条款变更

我们可修改本条款及本服务。本页始终显示现行版本及其生效日期。变更适用于其生效后下达的订单；在生效日
之后继续使用本服务，即表示您接受变更。

## 17. 法律适用与争议解决

本条款受运营方设立地法律管辖（不适用其冲突法规则），并由该地法院管辖；但您居住国法律赋予您提起诉讼
或受其法律保护的强制性权利除外。提起任何程序前，请先联系客服，以便我们尝试解决；大多数问题可通过
售后流程解决。如任何条款被认定为不可执行，其余条款继续有效。本条款连同上文链接的页面，构成您与我们之间
就本服务达成的完整协议。如译文与英文版本不一致，在法律允许的范围内以英文版本为准。

## 18. 联系我们

请通过店铺内的 **客服**（我的 → 联系客服）或订单内的聊天联系我们。
"""

PRIVACY_EN = f"""_{EFFECTIVE_EN}_

This policy explains what personal data Kesiv (the "Operator", "we") processes when you use this
website and its storefronts, why, and what you can do about it. Where a product is sold by an
independent merchant, that merchant receives the order data it needs to fulfil and support your
order and handles it under this policy and its own obligations.

## 1. What we collect

- **Account data:** email address or a generated handle, display name, language, a hashed
  password (never the password itself), and whether you signed in with Google.
- **Order data:** what you bought, prices, payment method, order status, delivery records and
  after-sales requests. Order information you type at checkout (for example an account name) is
  stored with your order and shown only to the staff handling it.
- **Payment data:** the payment provider's transaction reference and the amount. For
  cryptocurrency: the network, receiving address, amount and transaction hash, which are public
  on the blockchain. We never receive bank card numbers or wallet keys.
- **Messages and files:** customer-service chats, voice messages and images you send. Uploaded
  images are re-encoded and their metadata (such as location in EXIF) is removed.
- **Technical and security data:** IP address, browser user agent, request and error logs,
  rate-limit and verification records, and an audit log of sensitive actions.

## 2. Why we use it

- To provide the Service: accounts, orders, payment confirmation, delivery and after-sales
  (performance of our contract with you).
- To prevent and investigate fraud, payment abuse and attacks, keep evidence of transactions,
  and protect the Service, Sellers and other users (our legitimate interests).
- To meet legal, tax and accounting obligations, and to respond to lawful requests.
- To send messages you asked for or need: verification codes, password resets and order
  updates. We do not send marketing without your consent.

We do not sell your personal data and we do not use it for advertising profiles.

## 3. Cookies and browser storage

We use only what the store needs to work: a session cookie that keeps you signed in, a security
(CSRF) cookie, and a short-lived cookie recording that your browser passed the store's access
check. In your browser's local storage we keep your language, layout and media preferences, your
cart (products and quantities only — never the order information you type), recently viewed
products and, for the current tab, order access codes. We do not use advertising or analytics
trackers.

## 4. Who we share it with

- **Infrastructure:** our hosting provider (servers in Japan) and Cloudflare, which carries and
  protects traffic to the site and may provide security challenges.
- **Payments:** the payment provider you choose, which receives the order reference and amount;
  cryptocurrency payments are recorded on a public blockchain.
- **Communications:** our email provider, for verification and order emails; Google, if you
  choose to sign in with Google.
- **Sellers and staff:** the Seller of your order and our customer-service staff, limited to what
  they need. Staff alerts may contain an order number, product, amount and a masked email.
- **Authorities and payment networks:** where required by law, to establish or defend legal
  claims, or to investigate fraud and chargebacks.

These recipients may process data outside your country. We rely on their contractual and legal
safeguards.

## 5. How long we keep it

- Account data: until you delete your account.
- Orders and payment records: kept for accounting, tax and dispute purposes for as long as the
  law requires; when you delete your account they are kept in anonymised form.
- Chats and uploaded files: deleted or redacted when you delete your account, unless they are
  needed for an open dispute or a legal obligation.
- Security and audit logs: kept as long as needed to detect and investigate abuse and to meet
  legal obligations.
- Backups: rolling copies, each kept for up to 14 days.

## 6. Security

Codes held in stock for automatic delivery are encrypted and can only be decrypted by an isolated
delivery service; staff screens show masked values. Content delivered manually is stored with
your order and visible only to you and the staff handling it. Access to the admin console is restricted by network
and role. No system is perfectly secure; please keep your own credentials safe.

## 7. Your rights

Depending on where you live, you may have the right to access, correct, delete or export your
data, to object to or restrict certain processing, and to complain to a data-protection
authority. You can edit your profile and delete your account yourself in **My Account →
Settings**; for anything else contact customer service. We may need to verify your identity, and
we may keep data we are legally required or entitled to keep (for example to defend a claim).

## 8. Children

The Service is not intended for anyone under 18, and we do not knowingly collect their data.

## 9. Changes

We may update this policy. The current version and its effective date are always on this page.

## 10. Contact

Contact us through **Customer service** in the store (My Account → Contact customer service).
"""

PRIVACY_ZH = f"""_{EFFECTIVE_ZH}_

本政策说明 Kesiv（"运营方"或"我们"）在您使用本网站及其店铺时处理哪些个人信息、处理目的，以及您可以
采取的措施。商品由独立商家销售时，该商家将获得履行及售后所需的订单信息，并依本政策及其自身义务进行处理。

## 1. 我们收集的信息

- **账号信息：** 邮箱或系统生成的账号名、昵称、语言、经哈希处理的密码（绝不保存密码本身），以及您是否
  使用 Google 登录。
- **订单信息：** 购买的商品、价格、支付方式、订单状态、交付记录及售后申请。您在结账时填写的下单信息
  （例如账号名）随订单保存，仅向处理该订单的工作人员显示。
- **支付信息：** 支付服务商的交易参考号及金额。加密货币支付：网络、收款地址、金额及交易哈希，这些信息
  在区块链上公开。我们绝不接收银行卡号或钱包私钥。
- **消息与文件：** 客服聊天、语音消息及您发送的图片。上传的图片会被重新编码并移除元数据（例如 EXIF 中的
  位置信息）。
- **技术与安全信息：** IP 地址、浏览器标识、请求及错误日志、频率限制与验证记录，以及敏感操作的审计日志。

## 2. 使用目的

- 提供本服务：账号、订单、支付确认、交付及售后（履行与您之间的合同）。
- 预防和调查欺诈、支付滥用及攻击，保存交易证据，保护本服务、卖家及其他用户（我们的正当利益）。
- 履行法律、税务及会计义务，并回应合法要求。
- 发送您请求或必需的消息：验证码、密码重置及订单通知。未经您同意，我们不发送营销信息。

我们不出售您的个人信息，也不将其用于广告画像。

## 3. Cookie 与浏览器存储

我们仅使用店铺运行所必需的内容：保持登录状态的会话 Cookie、安全（CSRF）Cookie，以及记录您的浏览器
已通过店铺访问验证的短期 Cookie。在浏览器本地存储中，我们保存您的语言、布局及媒体偏好、购物车（仅商品
及数量，绝不包括您填写的下单信息）、最近浏览的商品，以及当前标签页内的订单访问码。我们不使用广告或
统计追踪工具。

## 4. 信息共享对象

- **基础设施：** 我们的托管服务商（服务器位于日本）以及为本站传输和保护流量、并可能提供安全验证的 Cloudflare。
- **支付：** 您选择的支付服务商，其将获得订单参考号及金额；加密货币支付会记录在公开区块链上。
- **通信：** 用于发送验证码及订单邮件的邮件服务商；如您选择 Google 登录，则包括 Google。
- **卖家与工作人员：** 您订单的卖家及我们的客服人员，仅限其所需范围。员工通知可能包含订单号、商品、金额
  及经遮盖的邮箱。
- **有关机关及支付网络：** 依法要求时、为确立或抗辩法律主张时，或为调查欺诈及拒付时。

上述接收方可能在您所在国家/地区以外处理信息，我们依赖其合同及法律保障措施。

## 5. 保存期限

- 账号信息：保存至您删除账号。
- 订单及支付记录：按法律要求的期限保存，用于会计、税务及争议处理；您删除账号后以匿名形式保存。
- 聊天及上传文件：您删除账号时删除或清除，除非因进行中的争议或法律义务需要保留。
- 安全及审计日志：在发现和调查滥用行为及履行法律义务所需期间内保存。
- 备份：滚动保存，每份最多保留 14 天。

## 6. 安全

用于自动发货的库存卡密经过加密，仅能由隔离的发货服务解密；员工界面仅显示遮盖后的内容。人工发货的内容
随订单保存，仅您及处理该订单的工作人员可见。管理后台的访问
受网络及角色限制。任何系统都无法做到绝对安全，请妥善保管您自己的凭据。

## 7. 您的权利

根据您所在地的法律，您可能有权访问、更正、删除或导出您的信息，反对或限制某些处理，并向数据保护机构
投诉。您可在 **我的 → 设置** 中自行修改资料及删除账号；其他请求请联系客服。我们可能需要核实您的身份，
并可能保留依法须保留或有权保留的信息（例如为抗辩索赔）。

## 8. 未成年人

本服务不面向 18 周岁以下人士，我们不会故意收集其信息。

## 9. 政策变更

我们可能更新本政策。本页始终显示现行版本及其生效日期。

## 10. 联系我们

请通过店铺内的 **客服**（我的 → 联系客服）联系我们。
"""


TERMS_EN, TERMS_ZH = _flow(TERMS_EN), _flow(TERMS_ZH)
PRIVACY_EN, PRIVACY_ZH = _flow(PRIVACY_EN), _flow(PRIVACY_ZH)
