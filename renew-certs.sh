#!/usr/bin/env bash
set -e

# Renew Let's Encrypt certificate for arthexis.com without reconfiguring services.
DOMAIN="arthexis.com"
LIVE_DIR="/etc/letsencrypt/live"
CERT_DIR="$LIVE_DIR/$DOMAIN"

# If a certificate already exists, show its details and determine the
# expiration date. Skip renewal when the certificate is more than 30 days
# away from expiry.
if [ -f "$CERT_DIR/fullchain.pem" ]; then
    echo "Current certificate details:"
    sudo openssl x509 -subject -issuer -enddate -noout -in "$CERT_DIR/fullchain.pem"
    EXPIRATION=$(sudo openssl x509 -enddate -noout -in "$CERT_DIR/fullchain.pem" | cut -d= -f2)
    EXP_EPOCH=$(date -d "$EXPIRATION" +%s)
    NOW_EPOCH=$(date +%s)
    DAYS_LEFT=$(( (EXP_EPOCH - NOW_EPOCH) / 86400 ))

    if [ "$DAYS_LEFT" -gt 30 ]; then
        echo "Renewal skipped: certificate for $DOMAIN valid until $EXPIRATION"
        exit 0
    fi
else
    echo "No existing certificate found for $DOMAIN in $CERT_DIR"
fi

echo "Stopping nginx if running…"
NGINX_RUNNING=false
if command -v systemctl >/dev/null && sudo systemctl is-active --quiet nginx; then
    NGINX_RUNNING=true
    sudo systemctl stop nginx
fi

echo "Requesting certificate renewal…"
# Using certonly to avoid modifying existing web server configuration.
if ! sudo certbot certonly --keep-until-expiring --standalone -d "$DOMAIN" --non-interactive; then
    echo "Certbot failed to obtain or renew the certificate. Check the output above or /var/log/letsencrypt/ for details." >&2
fi

echo "Checking for renewed certificate files…"
# After renewal, determine the latest certificate directory for the domain.
LATEST_DIR=$(sudo ls -1d "$LIVE_DIR/${DOMAIN}"* 2>/dev/null | sort | tail -n 1)

# Warn when no directory was produced, which usually indicates a certbot
# failure.
if [ -z "$LATEST_DIR" ]; then
    echo "No certificate directory found for $DOMAIN after running certbot." >&2
fi

# If Certbot placed the renewed certificate in a different directory (e.g. arthexis.com-0001),
# copy the relevant files back to the expected location.
if [ -n "$LATEST_DIR" ] && [ "$LATEST_DIR" != "$CERT_DIR" ]; then
    echo "Copying certificates from $LATEST_DIR to $CERT_DIR"
    sudo cp "$LATEST_DIR/fullchain.pem" "$CERT_DIR/fullchain.pem"
    sudo cp "$LATEST_DIR/privkey.pem" "$CERT_DIR/privkey.pem"
fi

# Display the new certificate's details if a file is present.
if [ -f "$CERT_DIR/fullchain.pem" ]; then
    echo "New certificate details:"
    sudo openssl x509 -subject -issuer -enddate -noout -in "$CERT_DIR/fullchain.pem"
fi

# Restart nginx if it was previously running.
if [ "$NGINX_RUNNING" = true ]; then
    echo "Restarting nginx"
    sudo systemctl start nginx
fi
