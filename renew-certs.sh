#!/usr/bin/env bash
set -e

# Renew Let's Encrypt certificate for arthexis.com without reconfiguring services.
DOMAIN="arthexis.com"
LIVE_DIR="/etc/letsencrypt/live"
CERT_DIR="$LIVE_DIR/$DOMAIN"

# Determine where the current certificate files live. Certbot may store
# them in a suffixed directory (e.g. "$DOMAIN-0001") on renewal.
# Use find under sudo so directory matching occurs with necessary permissions
EXISTING_DIR=$(sudo find "$LIVE_DIR" -maxdepth 1 -type d -name "${DOMAIN}*" 2>/dev/null | sort | tail -n 1)

if [ -n "$EXISTING_DIR" ]; then
    # Ensure the expected directory exists and contains the certificate.
    if [ "$EXISTING_DIR" != "$CERT_DIR" ]; then
        echo "Existing certificate found for $DOMAIN in $EXISTING_DIR"
        echo "Copying certificates to $CERT_DIR"
        sudo mkdir -p "$CERT_DIR"
        sudo cp "$EXISTING_DIR/fullchain.pem" "$CERT_DIR/fullchain.pem"
        sudo cp "$EXISTING_DIR/privkey.pem" "$CERT_DIR/privkey.pem"
    fi

    echo "Current certificate details:"
    sudo openssl x509 -subject -issuer -enddate -noout -in "$CERT_DIR/fullchain.pem"
    EXPIRATION=$(sudo openssl x509 -enddate -noout -in "$CERT_DIR/fullchain.pem" | cut -d= -f2)
    EXP_EPOCH=$(date -d "$EXPIRATION" +%s)
    NOW_EPOCH=$(date +%s)
    DAYS_LEFT=$(( (EXP_EPOCH - NOW_EPOCH) / 86400 ))
    echo "Days until renewal: $DAYS_LEFT"

    if [ "$DAYS_LEFT" -gt 30 ]; then
        echo "Renewal skipped: certificate for $DOMAIN valid until $EXPIRATION"
        # The certificate may have been copied from a suffixed directory. Reload
        # nginx so it serves the latest certificate even when no renewal is
        # performed.
        if command -v systemctl >/dev/null && sudo systemctl is-active --quiet nginx; then
            echo "Reloading nginx to apply the current certificate"
            sudo systemctl reload nginx
        fi
        exit 0
    fi
else
    echo "No existing certificate found for $DOMAIN in $LIVE_DIR"
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
LATEST_DIR=$(sudo find "$LIVE_DIR" -maxdepth 1 -type d -name "${DOMAIN}*" 2>/dev/null | sort | tail -n 1)

# Warn when no directory was produced, which usually indicates a certbot
# failure.
if [ -z "$LATEST_DIR" ]; then
    echo "No certificate directory found for $DOMAIN after running certbot." >&2
else
    # If Certbot placed the renewed certificate in a different directory, copy
    # the relevant files back to the expected location.
    if [ "$LATEST_DIR" != "$CERT_DIR" ]; then
        echo "Copying certificates from $LATEST_DIR to $CERT_DIR"
        sudo mkdir -p "$CERT_DIR"
        sudo cp "$LATEST_DIR/fullchain.pem" "$CERT_DIR/fullchain.pem"
        sudo cp "$LATEST_DIR/privkey.pem" "$CERT_DIR/privkey.pem"
    fi

    # Display the new certificate's details and days until renewal if a file is present.
    if [ -f "$CERT_DIR/fullchain.pem" ]; then
        echo "New certificate details:"
        sudo openssl x509 -subject -issuer -enddate -noout -in "$CERT_DIR/fullchain.pem"
        EXPIRATION=$(sudo openssl x509 -enddate -noout -in "$CERT_DIR/fullchain.pem" | cut -d= -f2)
        EXP_EPOCH=$(date -d "$EXPIRATION" +%s)
        NOW_EPOCH=$(date +%s)
        DAYS_LEFT=$(( (EXP_EPOCH - NOW_EPOCH) / 86400 ))
        echo "Days until renewal: $DAYS_LEFT"
    fi
fi

# Restart nginx if it was previously running.
if [ "$NGINX_RUNNING" = true ]; then
    echo "Restarting nginx"
    sudo systemctl start nginx
fi
