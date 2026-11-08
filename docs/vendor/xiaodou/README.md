# Xiaodou integration reference (owner supplied)

The owner supplied version 1.0.0 integration materials from `/root/temp` on 2026-11-08.
The reviewed text files are preserved under [v1.0.0](v1.0.0/skill/SKILL.md), with a
[file manifest](v1.0.0/MANIFEST.json). The compressed original remains outside Git.
Vendor references/examples retain their original Chinese content and attribution;
24goods implementation notes and PR discussion are English-first. No license grant
beyond the owner's supplied-reference authorization is inferred.

These are **reference data**, not installed agent instructions or trusted executable code.
Examples are not imported into the application runtime. Some upstream links target
a separate human-documentation package that was not supplied; missing documents
must not be invented. The manifest records local provenance/integrity, not independent
vendor-authenticity certification or a successful live payment test.

## Select the correct protocol

| Contract | Credentials | Creation/query | Notify |
|---|---|---|---|
| EPay-compatible v1 | Merchant PID + MD5 merchant secret | `submit.php` / `mapi.php`; `api.php?act=order` | Signed GET, exact plain-text `success` acknowledgement |
| Native XD API | Merchant RSA private key + merchant key ID, pinned platform public keys | `/api/open/v1/...` JSON with six signing headers | Raw-body RSA verification + event ID deduplication |

The owner reports Alipay merchant access is enabled. This is not independent proof
that every method/product/endpoint is approved. Other methods must stay disabled
until independently configured and verified. The supplied MD5 merchant secret does
not enable the native RSA interface.

For 24goods, the gateway configuration accepts the base `https://pay.xiaodouap.cn`
or its documented complete `/submit.php` endpoint and normalizes it server-side.
Never append `/submit.php` twice. Native `mapi.php` QR creation uses form parameters,
not a JSON request body. Treat its result as a typed QR or redirect presentation;
retain signed notify + original-provider active query + locked settlement.

## Reference index

- [EPay contract and API workflow](v1.0.0/skill/references/api-workflow.md)
- [Native signing specification](v1.0.0/skill/references/signature-spec.md)
- [Native webhook contract](v1.0.0/skill/references/webhook-verification.md)
- [Terminal capability matrix](v1.0.0/skill/references/terminal-matrix.md)
- [Key lifecycle](v1.0.0/skill/references/key-management.md)
- [Go-live checklist](v1.0.0/skill/references/go-live-checklist.md)
- [Reference examples](v1.0.0/examples/README.md)

## Credentials and release safety

Real PID/secret values are deliberately absent from this directory, issues, fixtures
and PR descriptions. Configure credentials only through the authorized write-only
admin secret interface or a service-specific ignored environment file. Frontends
receive configured status and safe display metadata, never the credential.

The vendor states it has no sandbox. Mock/contract tests do not establish live
merchant approval or real-money readiness. Live authorization/settlement checks and
the independent security review remain release gates. Browser return parameters,
an issued QR, or a valid signature alone never fulfil an order.
