#!/usr/bin/env bash
# #72 S13: compare Cloudflare's published edge ranges with deploy/Caddyfile.prod trusted_proxies.
# Exit 0 = identical, 1 = drift (prints the difference), 2 = could not fetch the lists.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
want=$(mktemp); have=$(mktemp); trap 'rm -f "$want" "$have"' EXIT
{ curl -fsS --max-time 15 https://www.cloudflare.com/ips-v4 && echo \
  && curl -fsS --max-time 15 https://www.cloudflare.com/ips-v6 && echo; } 2>/dev/null \
  | tr -s ' \r\n' '\n' | grep -E '^[0-9a-f:.]+/[0-9]+$' | sort -u > "$want" || true
[[ -s "$want" ]] || { echo "could not fetch Cloudflare IP lists" >&2; exit 2; }
grep -E '^\s*trusted_proxies static' "$ROOT/deploy/Caddyfile.prod" | head -1 \
  | tr -s ' \t' '\n' | grep -E '^[0-9a-f:.]+/[0-9]+$' | sort -u > "$have"
if diff -u "$have" "$want" >/dev/null; then
  echo "Cloudflare ranges match deploy/Caddyfile.prod ($(wc -l < "$want") prefixes)"
  exit 0
fi
echo "Cloudflare ranges DRIFTED from deploy/Caddyfile.prod (- ours, + Cloudflare):"
diff -u "$have" "$want" | grep -E '^[+-][0-9a-f]' || true
echo "Fix: docs/production.md 'Refreshing Cloudflare IP ranges'."
exit 1
