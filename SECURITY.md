# Security policy

Kesiv (24goods) handles payments and digital goods. We take reports seriously.

## Reporting a vulnerability

- **Do not open a public issue, pull request or discussion** for a vulnerability.
- Report it privately through GitHub: **Security → Report a vulnerability** on this repository
  (private vulnerability reporting), or email **support@kesiv.com** with the subject
  `SECURITY`.
- Include what you found, how to reproduce it, and the impact you expect. Do not access,
  modify or delete data that is not yours, do not run denial-of-service or social-engineering
  tests, and do not test against real payments.

We aim to acknowledge a report within 3 working days and to agree a disclosure date with you
once a fix is available.

## Scope

In scope: the code in this repository and the store it runs (`shop.kesiv.com`).
Out of scope: third-party payment gateways and services we integrate with (report those to
the vendor), and findings that need a compromised device or account.

## For contributors

The non-negotiable security invariants are in [`AGENTS.md`](AGENTS.md) §1. Changes to the
paths listed in [`.github/CODEOWNERS`](.github/CODEOWNERS) need an independent security review.
