#!/usr/bin/env bash
# Deploy dist/ to Cloudflare Pages. Auto-discovers the account id and creates the project on first run.
set -euo pipefail
PROJECT=${PAGES_PROJECT:-ai-paper-radar}
API=https://api.cloudflare.com/client/v4
AUTH="Authorization: Bearer ${CLOUDFLARE_API_TOKEN:?missing CLOUDFLARE_API_TOKEN secret}"
if [ -z "${CLOUDFLARE_ACCOUNT_ID:-}" ]; then
  CLOUDFLARE_ACCOUNT_ID=$(curl -fsS -H "$AUTH" "$API/accounts?per_page=5" | jq -r '.result[0].id')
  echo "discovered account id: ${CLOUDFLARE_ACCOUNT_ID:0:6}…"
fi
export CLOUDFLARE_ACCOUNT_ID
P="$API/accounts/$CLOUDFLARE_ACCOUNT_ID/pages/projects"
if ! curl -fsS -H "$AUTH" "$P/$PROJECT" -o /tmp/project.json; then
  echo "creating Pages project $PROJECT"
  curl -fsS -X POST -H "$AUTH" -H "Content-Type: application/json" "$P" \
    -d "{\"name\":\"$PROJECT\",\"production_branch\":\"main\"}" -o /tmp/project.json
fi
SUB=$(jq -r '.result.subdomain' /tmp/project.json)
npx --yes wrangler@4 pages deploy dist --project-name "$PROJECT" --branch main --commit-dirty=true
echo "site=https://$SUB" | tee -a "${GITHUB_OUTPUT:-/dev/null}"
