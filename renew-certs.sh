#!/usr/bin/env bash
set -e

# Renew Let's Encrypt certificate for arthexis.com without reconfiguring services.
DOMAIN="arthexis.com"
LIVE_DIR="/etc/letsencrypt/live"
CERT_DIR="$LIVE_DIR/$DOMAIN"

# Stop nginx if it's running to free up port 80 for the standalone server.
NGINX_RUNNING=false
if command -v systemctl >/dev/null && sudo systemctl is-active --quiet nginx; then
    NGINX_RUNNING=true
    sudo systemctl stop nginx
fi

# Request renewal if the certificate is close to expiring.
# Using certonly to avoid modifying existing web server configuration.
sudo certbot certonly --keep-until-expiring --quiet --standalone -d "$DOMAIN" --non-interactive || true

# After renewal, determine the latest certificate directory for the domain.
LATEST_DIR=$(sudo ls -1d "$LIVE_DIR/${DOMAIN}"* 2>/dev/null | sort | tail -n 1)

# If Certbot placed the renewed certificate in a different directory (e.g. arthexis.com-0001),
# copy the relevant files back to the expected location.
if [ -n "$LATEST_DIR" ] && [ "$LATEST_DIR" != "$CERT_DIR" ]; then
    sudo cp "$LATEST_DIR/fullchain.pem" "$CERT_DIR/fullchain.pem"
    sudo cp "$LATEST_DIR/privkey.pem" "$CERT_DIR/privkey.pem"
fi

# Display the new certificate's expiration date.
if [ -f "$CERT_DIR/fullchain.pem" ]; then
    EXPIRATION=$(sudo openssl x509 -enddate -noout -in "$CERT_DIR/fullchain.pem" | cut -d= -f2)
    echo "Certificate for $DOMAIN expires on: $EXPIRATION"
fi

# Restart nginx if it was previously running.
if [ "$NGINX_RUNNING" = true ]; then
    sudo systemctl start nginx
fi
