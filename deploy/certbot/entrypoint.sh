#!/bin/sh
set -eu

domain="${VESPER_DOMAIN:?VESPER_DOMAIN is required}"
email="${CERTBOT_EMAIL:?CERTBOT_EMAIL is required}"
cert="/etc/letsencrypt/live/${domain}/fullchain.pem"

if [ ! -f "$cert" ]; then
  certbot certonly \
    --webroot -w /var/www/certbot \
    -d "$domain" \
    --email "$email" \
    --agree-tos \
    --no-eff-email \
    --non-interactive
fi

while :; do
  certbot renew --webroot -w /var/www/certbot --quiet
  sleep 12h
done
