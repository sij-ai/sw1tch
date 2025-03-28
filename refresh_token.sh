#!/bin/bash

# File paths
BASE_PATH="/home/sij/hand_of_morpheus"
TOKEN_FILE="$BASE_PATH/.registration_token"
LOG_FILE="$BASE_PATH/token_refresh.log"
BACKUP_PATH="/home/sij/conduwuit_backup"

# Server/domain info
SERVER_DOMAIN="we2.ee"
CONTAINER_NAME="conduwuit"
CONTAINER_IMAGE="conduwuit:custom"
ADDRESS='["0.0.0.0", "::"]'
PORT=8008

# Auto-join room configuration
AUTO_JOIN_ROOMS='["#server:we2.ee"]'

# Function to log with timestamp to both file and terminal
log() {
    local message="$(date --iso-8601=seconds) $1"
    echo "$message" >> "$LOG_FILE"  # Write to log file
    echo "$message"  # Print to terminal
}

# Generate new token (6 random hex characters)
NEW_TOKEN=$(openssl rand -hex 3)

# Write new token to file without newline
echo -n "$NEW_TOKEN" > "$TOKEN_FILE"
if [ $? -ne 0 ]; then
    log "ERROR: Failed to write new token to $TOKEN_FILE"
    exit 1
fi

log "Generated new registration token"

# Stop and remove existing container
docker stop "$CONTAINER_NAME" 2>/dev/null
docker rm "$CONTAINER_NAME" 2>/dev/null

# Launch new container
docker run -d \
  -v "db:/var/lib/conduwuit/" \
  -v "${TOKEN_FILE}:/.registration_token:ro" \
  -v "${BACKUP_PATH}:/backup" \
  -e CONDUWUIT_SERVER_NAME="$SERVER_DOMAIN" \
  -e CONDUWUIT_DATABASE_PATH="/var/lib/conduwuit/conduwuit.db" \
  -e CONDUWUIT_DATABASE_BACKUP_PATH="/backup" \
  -e CONDUWUIT_ALLOW_REGISTRATION=true \
  -e CONDUWUIT_REGISTRATION_TOKEN_FILE="/.registration_token" \
  -e CONDUWUIT_ADDRESS="$ADDRESS" \
  -e CONDUWUIT_PORT="$PORT" \
  -e CONDUWUIT_NEW_USER_DISPLAYNAME_SUFFIX="" \
  -e CONDUWUIT_AUTO_JOIN_ROOMS="$AUTO_JOIN_ROOMS" \
  -e CONDUWUIT_FORGET_FORCED_UPON_LEAVE=true \
  -e CONDUWUIT_DB_CACHE_CAPACITY_MB=1024 \
  -e CONDUWUIT_DB_WRITE_BUFFER_CAPACITY_MB=256 \
  -e CONDUWUIT_DB_POOL_WORKERS=64 \
  -e CONDUWUIT_DB_POOL_WORKERS_LIMIT=128 \
  -e CONDUWUIT_STREAM_AMPLIFICATION=8192 \
  -e CONDUWUIT_MAX_REQUEST_SIZE=33554432 \
  -e CONDUWUIT_CACHE_CAPACITY_MODIFIER=1.5 \
  -e CONDUWUIT_ALLOW_FEDERATION=true \
  -e CONDUWUIT_ALLOW_PUBLIC_ROOM_DIRECTORY_OVER_FEDERATION=true \
  -e CONDUWUIT_ALLOW_PUBLIC_ROOM_DIRECTORY_WITHOUT_AUTH=true \
  -e CONDUWUIT_WELL_KNOWN_CONN_TIMEOUT=30 \
  -e CONDUWUIT_FEDERATION_TIMEOUT=600 \
  -e CONDUWUIT_FEDERATION_IDLE_TIMEOUT=60 \
  -e CONDUWUIT_SENDER_TIMEOUT=600 \
  -e CONDUWUIT_SENDER_IDLE_TIMEOUT=360 \
  -e CONDUWUIT_SENDER_SHUTDOWN_TIMEOUT=30 \
  -e CONDUWUIT_DNS_CACHE_ENTRIES=1000 \
  -e CONDUWUIT_DNS_MIN_TTL=300 \
  -e CONDUWUIT_DNS_MIN_TTL_NXDOMAIN=600 \
  -e CONDUWUIT_DNS_TCP_FALLBACK=true \
  -e CONDUWUIT_IP_LOOKUP_STRATEGY=3 \
  -e RUST_LOG="conduwuit=trace,reqwest=trace,hickory_proto=trace" \
  --network host \
  --name "$CONTAINER_NAME" \
  --restart unless-stopped \
  "$CONTAINER_IMAGE"
if [ $? -ne 0 ]; then
    log "ERROR: Failed to create new conduwuit container"
    exit 1
fi

log "Successfully recreated container \"$CONTAINER_NAME\" with image \"$CONTAINER_IMAGE\" and these parameters:"
log " - domain: $SERVER_DOMAIN"
log " - address: $ADDRESS"
log " - port: $PORT"
log " - auto-join rooms: $AUTO_JOIN_ROOMS"
