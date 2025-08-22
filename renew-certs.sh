#!/usr/bin/env bash
set -e

# Renew Let's Encrypt certificate for arthexis.com without reconfiguring services.
DOMAIN="arthexis.com"
LIVE_DIR="/etc/letsencrypt/live"
CERT_DIR="$LIVE_DIR/$DOMAIN"

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
