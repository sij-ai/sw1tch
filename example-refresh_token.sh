#!/bin/bash

# Path configuration
TOKEN_FILE="/path/to/hand_of_morpheus/.registration_token"
LOG_FILE="/path/to/hand_of_morpheus/token_refresh.log"

# Function to log with timestamp
log() {
    echo "$(date --iso-8601=seconds) $1" >> "$LOG_FILE"
}

# Generate new token (32 random hex characters)
NEW_TOKEN=$(openssl rand -hex 16)

# Write new token to file without newline
echo -n "$NEW_TOKEN" > "$TOKEN_FILE"
if [ $? -ne 0 ]; then
    log "ERROR: Failed to write new token to $TOKEN_FILE"
    exit 1
fi

log "Generated new registration token"

# Recreate conduwuit container
docker stop conduwuit
docker rm conduwuit

docker run -d \
  -p 127.0.0.1:8448:6167 \
  -v db:/var/lib/conduwuit/ \
  -v "${TOKEN_FILE}:/.registration_token:ro" \
  -e CONDUWUIT_SERVER_NAME="your.domain" \
  -e CONDUWUIT_DATABASE_PATH="/var/lib/conduwuit/conduwuit.db" \
  -e CONDUWUIT_DATABASE_BACKUP_PATH="/var/lib/conduwuit/backup" \
  -e CONDUWUIT_ALLOW_REGISTRATION=true \
  -e CONDUWUIT_REGISTRATION_TOKEN_FILE="/.registration_token" \
  -e CONDUWUIT_PORT=6167 \
  -e CONDUWUIT_ADDRESS="0.0.0.0" \
  -e CONDUWUIT_NEW_USER_DISPLAYNAME_SUFFIX="" \
  -e CONDUWUIT_ALLOW_PUBLIC_ROOM_DIRECTORY_OVER_FEDERATION=true \
  -e CONDUWUIT_ALLOW_PUBLIC_ROOM_DIRECTORY_WITHOUT_AUTH=true \
  -e CONDUWUIT_ALLOW_FEDERATION=true \
  -e CONDUWUIT_AUTO_JOIN_ROOMS='["#community:your.domain","#welcome:your.domain"]' \
  --name conduwuit \
  --restart unless-stopped \
  ghcr.io/girlbossceo/conduwuit:v0.5.0-rc2-e5049cae4a3890dc5f61ead53281f23b36bf4c97

if [ $? -ne 0 ]; then
    log "ERROR: Failed to create new conduwuit container"
    exit 1
fi

log "Successfully recreated conduwuit container with new token"
