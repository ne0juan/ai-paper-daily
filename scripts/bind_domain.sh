#!/usr/bin/env bash
# Attach a custom domain (apex + www) to the Pages project and create the DNS records
# if the zone lives in the same Cloudflare account. Usage: scripts/bind_domain.sh example.com
set -uo pipefail
DOMAIN=${1:?domain}
PROJECT=${PAGES_PROJECT:-ai-paper-radar}
API=https://api.cloudflare.com/client/v4
AUTH="Authorization: Bearer ${CLOUDFLARE_API_TOKEN:?missing token}"
ACC=${CLOUDFLARE_ACCOUNT_ID:-$(curl -fsS -H "$AUTH" "$API/accounts?per_page=5" | jq -r '.result[0].id')}
TARGET="$PROJECT.pages.dev"
echo "== token permissions check"; curl -sS -H "$AUTH" "$API/user/tokens/verify" | jq -c '.result // .errors'
for NAME in "$DOMAIN" "www.$DOMAIN"; do
  echo "== attach $NAME to Pages project"
  curl -sS -X POST -H "$AUTH" -H "Content-Type: application/json" \
    "$API/accounts/$ACC/pages/projects/$PROJECT/domains" -d "{\"name\":\"$NAME\"}" | jq -c '{success, errors, status: .result.status}'
done
echo "== zone lookup"
ZONE=$(curl -sS -H "$AUTH" "$API/zones?name=$DOMAIN" | jq -r '.result[0].id // empty')
if [ -z "$ZONE" ]; then
  echo "zone $DOMAIN not visible to this token (not added to Cloudflare yet, or token lacks Zone read)"; 
else
  for NAME in "$DOMAIN" "www.$DOMAIN"; do
    EXIST=$(curl -sS -H "$AUTH" "$API/zones/$ZONE/dns_records?name=$NAME" | jq -r '.result[0].id // empty')
    BODY="{\"type\":\"CNAME\",\"name\":\"$NAME\",\"content\":\"$TARGET\",\"proxied\":true,\"ttl\":1}"
    if [ -n "$EXIST" ]; then
      echo "== update DNS $NAME"; curl -sS -X PUT -H "$AUTH" -H "Content-Type: application/json" "$API/zones/$ZONE/dns_records/$EXIST" -d "$BODY" | jq -c '{success, errors}'
    else
      echo "== create DNS $NAME"; curl -sS -X POST -H "$AUTH" -H "Content-Type: application/json" "$API/zones/$ZONE/dns_records" -d "$BODY" | jq -c '{success, errors}'
    fi
  done
fi
echo "== domain status"
curl -sS -H "$AUTH" "$API/accounts/$ACC/pages/projects/$PROJECT/domains" | jq -c '[.result[] | {name, status, verification: .verification_data.status, validation: .validation_data.status}]'
