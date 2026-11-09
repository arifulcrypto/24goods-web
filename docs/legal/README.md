# Legal notices — what exists, and what still needs a lawyer

Status: **ownership asserted, terms not yet drafted.** Issue #40.

## What this change added

| Artefact | Purpose |
|---|---|
| `/LICENSE` | Proprietary, all-rights-reserved notice. Before this, the repository had **no** stated license. |
| `/robots.txt` | Search crawlers still allowed; agents that collect for model training get `Disallow: /`. |
| `/ai.txt` | Machine-readable statement of the permissions the LICENSE withholds. |
| Console notice | One-time ownership and anti-abuse notice in both SPAs. |

## Two things the owner must do

1. **Replace the copyright holder name.** `LICENSE` currently says `Kesiv`, taken from the
   repository's own branding, and the console notice passes `owner: 'Kesiv'`. Substitute the exact
   legal name of the owning person or company. A notice naming an entity that does not legally
   exist is weaker than one naming the individual owner.

2. **Have a qualified lawyer review anything that governs real transactions.** Nothing here is
   legal advice. The items below in particular are jurisdiction-specific and a wrong answer is
   worse than no clause at all, because an unenforceable term protects nothing while creating an
   impression of coverage.

## Terms of Service — still to draft, and the open questions

The owner asked for terms "modeled after Anthropic's". **Their text will not be copied**: that
would infringe their copyright, in a change whose entire purpose is asserting ours. Original text
in a comparable structure (plain language, numbered sections, explicit acceptable-use and
termination clauses) is the plan instead.

Blocking questions, none of which can be answered from the codebase:

- **Governing law and venue.** The host is in Japan (IIJ), the owner's jurisdiction is unstated,
  and buyers are international. This determines almost everything else.
- **Selling entity.** A named company, or an individual trading under a brand?
- **Mainland China restriction.** The owner wants Mainland China users and orders excluded. As a
  business decision this is legitimate and consistent with the existing §1.7 hosting invariant.
  Open: whether exclusion is by stated terms only, or enforced at checkout (a separate issue — the
  legal text should not be blocked on the enforcement work), and how it interacts with consumer
  law in the jurisdiction finally chosen.
- **Digital-goods consumer rights.** Many jurisdictions grant statutory withdrawal/refund rights
  that cannot be waived by terms, with a specific exception for digital content supplied
  immediately with the buyer's acknowledgement. The card-key delivery model depends on getting
  this right.
- **Refunds, chargebacks and after-sales.** The code already implements an after-sales window; the
  terms must match what the software actually does, not the reverse.
- **Privacy notice.** `LICENSE` §6 commits to logging request metadata, IP and user agent, and
  says retention and lawful basis live in a per-deployment privacy notice. That notice does not
  exist yet. Where personal data is involved this is a legal obligation in most jurisdictions, not
  a nice-to-have.
- **AML / sanctions for USDT.** Accepting crypto raises obligations that vary sharply by
  jurisdiction and are out of scope for a drafting pass.

## On DevTools blocking

Requested, and deliberately **not** built. Reasoning is recorded in #40 and in the header comment
of `frontend/packages/shared/src/notice.ts`. In short: every line shipped to a browser is already
on the visitor's machine and `curl` retrieves the bundle without executing any defence, so the
technique stops no one who is actually copying the work, while breaking assistive technology,
hanging some mobile browsers, resembling malware to security scanners, and blocking our own staff
from diagnosing a customer's payment problem.

What protects this work is the license, enforceable terms, and the server-side logic that is never
shipped to the browser — the card vault, the notify pipeline and the tenant isolation, which is
where this project's real value already lives.

## Terms of service and privacy policy (2026-11-09)

Source: `backend/app/devtools/legal_content.py` (EN + zh-CN), seeded as the `terms` and `privacy`
pages. The owner approved replacing the "have a lawyer review this" template banner (#48 §5, #40).
The text favours the operator and sellers within what consumer law allows: delivery is final once
made (immediate-delivery consent), after-sales is the single remedy channel, chargebacks without
using it carry consequences, crypto payment errors sit with the sender, liability is capped at
the order price, and logs/chat/delivery records are evidence. Clauses that cannot be contracted
out of are expressly preserved, because an overreaching clause tends to be struck down whole.

The privacy policy lists only what the code does. Keep it in step: buyer order information and
manual delivery text are stored in plaintext today (#8), and backups are not yet encrypted (#28).
Update the policy when those land.

Open owner decision: §17 names "the place where the Operator is established" as governing law
and venue. Replace it with a specific jurisdiction once the operating entity is fixed. A local
lawyer's review is still recommended before relying on the text in a dispute.

Live sites: pages are seeded once and never overwritten, so a change to this file needs the page
edited in Admin -> Articles (or the same PATCH via the admin API) on each deployment.
