#!/usr/bin/env bash
# deploy.sh — full Cloudflare deployment for ucp-agent stack
# Usage: CLOUDFLARE_API_TOKEN=<token> bash deploy.sh
# Secrets (GLM_API_KEY, AGENT_SECRET) are prompted interactively.
set -euo pipefail

ACCOUNT_ID="0356d59ccdcff356bf3bbdb580bbaa60"

log() { echo "▶ $*"; }

# ── 0. Auth check ─────────────────────────────────────────────────────────────
if [[ -z "${CLOUDFLARE_API_TOKEN:-}" ]]; then
  echo "ERROR: set CLOUDFLARE_API_TOKEN before running this script."
  exit 1
fi
wrangler whoami

# ── 1. Create KV namespaces (idempotent: grep existing first) ─────────────────
log "Creating KV namespace: WELFARE_KV"
WELFARE_EXISTING=$(wrangler kv:namespace list 2>/dev/null | \
  python3 -c "import sys,json; ns=json.load(sys.stdin); \
  print(next((n['id'] for n in ns if n['title']=='ucp-agent-WELFARE_KV'), ''))" 2>/dev/null || true)

if [[ -z "$WELFARE_EXISTING" ]]; then
  WELFARE_ID=$(wrangler kv:namespace create WELFARE_KV --account-id "$ACCOUNT_ID" 2>&1 | \
    grep -o '"id": "[^"]*"' | head -1 | cut -d'"' -f4)
else
  WELFARE_ID="$WELFARE_EXISTING"
  log "  WELFARE_KV already exists: $WELFARE_ID"
fi
log "  WELFARE_KV id = $WELFARE_ID"

log "Creating KV namespace: SC_KV"
SC_EXISTING=$(wrangler kv:namespace list 2>/dev/null | \
  python3 -c "import sys,json; ns=json.load(sys.stdin); \
  print(next((n['id'] for n in ns if n['title']=='supply-chain-mock-SC_KV'), ''))" 2>/dev/null || true)

if [[ -z "$SC_EXISTING" ]]; then
  SC_ID=$(wrangler kv:namespace create SC_KV \
    --config "$(dirname "$0")/supply-chain-worker/wrangler.jsonc" 2>&1 | \
    grep -o '"id": "[^"]*"' | head -1 | cut -d'"' -f4)
else
  SC_ID="$SC_EXISTING"
  log "  SC_KV already exists: $SC_ID"
fi
log "  SC_KV id = $SC_ID"

# ── 2. Patch wrangler.jsonc with real KV IDs ──────────────────────────────────
AGENT_CFG="$(dirname "$0")/agent-worker/wrangler.jsonc"
SC_CFG="$(dirname "$0")/supply-chain-worker/wrangler.jsonc"

python3 - <<EOF
import json, re

def patch_kv(path, binding, new_id):
    with open(path) as f:
        content = f.read()
    # Strip JSONC comments for parsing
    stripped = re.sub(r'//.*', '', content)
    obj = json.loads(stripped)
    for ns in obj.get('kv_namespaces', []):
        if ns['binding'] == binding:
            ns['id'] = new_id
    with open(path, 'w') as f:
        json.dump(obj, f, indent=2)
    print(f"  Patched {path}: {binding} = {new_id}")

patch_kv('$AGENT_CFG', 'WELFARE_KV', '$WELFARE_ID')
patch_kv('$SC_CFG', 'SC_KV', '$SC_ID')
EOF

# ── 3. Deploy supply-chain-mock worker ────────────────────────────────────────
log "Deploying supply-chain-mock worker..."
(cd "$(dirname "$0")/supply-chain-worker" && wrangler deploy)
log "  supply-chain-mock deployed."

# ── 4. Set secrets on ucp-agent worker ───────────────────────────────────────
log "Setting secrets on ucp-agent worker..."
echo "Enter GLM_API_KEY (input hidden):"
read -rs GLM_KEY
echo "$GLM_KEY" | wrangler secret put GLM_API_KEY \
  --config "$(dirname "$0")/agent-worker/wrangler.jsonc"

echo "Enter AGENT_SECRET (press enter for auto-generated):"
read -rs AGENT_SECRET_VAL
if [[ -z "$AGENT_SECRET_VAL" ]]; then
  AGENT_SECRET_VAL=$(python3 -c "import secrets; print(secrets.token_hex(32))")
  log "  Auto-generated AGENT_SECRET: $AGENT_SECRET_VAL"
fi
echo "$AGENT_SECRET_VAL" | wrangler secret put AGENT_SECRET \
  --config "$(dirname "$0")/agent-worker/wrangler.jsonc"

# ── 5. Deploy ucp-agent worker ────────────────────────────────────────────────
log "Deploying ucp-agent worker..."
(cd "$(dirname "$0")/agent-worker" && wrangler deploy)
log "  ucp-agent deployed."

log ""
log "✅ Deployment complete!"
log "   Agent URL: https://ucp-agent.<your-subdomain>.workers.dev"
log ""
log "Next steps:"
log "  • Set SLACK_BOT_TOKEN if you want Slack notifications:"
log "    echo '<token>' | wrangler secret put SLACK_BOT_TOKEN --config cloudflare/agent-worker/wrangler.jsonc"
log "  • Test the agent at the worker URL above"
