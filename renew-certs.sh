#!/usr/bin/env bash
set -e

# Renew Let's Encrypt certificate for arthexis.com without reconfiguring services.
DOMAIN="arthexis.com"
LIVE_DIR="/etc/letsencrypt/live"
CERT_DIR="$LIVE_DIR/$DOMAIN"

# If a certificate already exists, determine the expiration date and skip
# renewal when it is more than 30 days away.
if [ -f "$CERT_DIR/fullchain.pem" ]; then
    EXPIRATION=$(sudo openssl x509 -enddate -noout -in "$CERT_DIR/fullchain.pem" | cut -d= -f2)
    EXP_EPOCH=$(date -d "$EXPIRATION" +%s)
    NOW_EPOCH=$(date +%s)
    DAYS_LEFT=$(( (EXP_EPOCH - NOW_EPOCH) / 86400 ))

    if [ "$DAYS_LEFT" -gt 30 ]; then
        echo "Renewal skipped: certificate for $DOMAIN valid until $EXPIRATION"
        exit 0
    fi
fi

echo "Stopping nginx if running…"
NGINX_RUNNING=false
if command -v systemctl >/dev/null && sudo systemctl is-active --quiet nginx; then
    NGINX_RUNNING=true
    sudo systemctl stop nginx
fi

echo "Requesting certificate renewal…"
# Using certonly to avoid modifying existing web server configuration.
sudo certbot certonly --keep-until-expiring --quiet --standalone -d "$DOMAIN" --non-interactive || true

echo "Checking for renewed certificate files…"
# After renewal, determine the latest certificate directory for the domain.
LATEST_DIR=$(sudo ls -1d "$LIVE_DIR/${DOMAIN}"* 2>/dev/null | sort | tail -n 1)

# If Certbot placed the renewed certificate in a different directory (e.g. arthexis.com-0001),
# copy the relevant files back to the expected location.
if [ -n "$LATEST_DIR" ] && [ "$LATEST_DIR" != "$CERT_DIR" ]; then
    echo "Copying certificates from $LATEST_DIR to $CERT_DIR"
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
    echo "Restarting nginx"
    sudo systemctl start nginx
fi
