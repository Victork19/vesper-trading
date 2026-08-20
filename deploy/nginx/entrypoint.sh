#!/bin/sh
set -eu

DOMAIN="${VESPER_DOMAIN:-vesper-scar.duckdns.org}"
CONF_DIR=/etc/nginx/conf.d
HTTP_CONF=/opt/vesper/nginx-http.conf
HTTPS_CONF=/opt/vesper/nginx-https.conf
CERT=/etc/letsencrypt/live/${DOMAIN}/fullchain.pem

render() {
  if [ -f "$CERT" ]; then
    sed "s/__VESPER_DOMAIN__/${DOMAIN}/g" "$HTTPS_CONF" > "$CONF_DIR/default.conf"
    echo "nginx mode: https"
  else
    sed "s/__VESPER_DOMAIN__/${DOMAIN}/g" "$HTTP_CONF" > "$CONF_DIR/default.conf"
    echo "nginx mode: http; waiting for certificate"
  fi
}

render
nginx -g 'daemon off;' &
NGINX_PID=$!
CURRENT="$(test -f "$CERT" && echo https || echo http)"
CERT_MTIME="$(stat -c %Y "$CERT" 2>/dev/null || echo 0)"

while kill -0 "$NGINX_PID" 2>/dev/null; do
  sleep 15
  NEXT="$(test -f "$CERT" && echo https || echo http)"
  if [ "$NEXT" != "$CURRENT" ]; then
    render
    nginx -s reload
    CURRENT="$NEXT"
  fi
  NEXT_MTIME="$(stat -c %Y "$CERT" 2>/dev/null || echo 0)"
  if [ "$NEXT" = "https" ] && [ "$NEXT_MTIME" != "$CERT_MTIME" ]; then
    nginx -s reload
    CERT_MTIME="$NEXT_MTIME"
  fi
done
wait "$NGINX_PID"
