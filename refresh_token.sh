#!/bin/bash

# File paths
BASE_PATH="/home/sij/hand_of_morpheus"
TOKEN_FILE="$BASE_PATH/.registration_token"
LOG_FILE="$BASE_PATH/token_refresh.log"
BACKUP_PATH="/home/sij/conduwuit_backup"

# Server configuration
SERVER_DOMAIN="we2.ee"
HOST_PORT=8448
CONTAINER_PORT=6167
CONTAINER_NAME="conduwuit"
CONTAINER_IMAGE="ghcr.io/girlbossceo/conduwuit:v0.5.0-rc3-b6e9dc3d98704c56027219d3775336910a0136c6"

# Performance tuning
DB_READ_CACHE_MB=16384        # 16GB for read cache
DB_WRITE_BUFFER_MB=2048       # 2GB write buffer
CACHE_MODIFIER=4.0            # 4x default LRU caches
DB_POOL_WORKERS=128           # Optimized for NVMe
STREAM_WIDTH_SCALE=2.0        # Concurrent operations scaling
STREAM_AMPLIFICATION=4096     # Batch size for operations
MAX_REQUEST_SIZE=104857600    # 100MB uploads
BLURHASH_MAX_SIZE=134217728   # 128MB for blurhash processing

# Auto-join room configuration
AUTO_JOIN_ROOMS="[\"#pub:$SERVER_DOMAIN\",\"#home:$SERVER_DOMAIN\"]"

# Function to log with timestamp
log() {
    echo "$(date --iso-8601=seconds) $1" >> "$LOG_FILE"
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

# Recreate conduwuit container
docker stop $CONTAINER_NAME
docker rm $CONTAINER_NAME

docker run -d \
  -p 0.0.0.0:${HOST_PORT}:${CONTAINER_PORT} \
  -v db:/var/lib/conduwuit/ \
  -v "${TOKEN_FILE}:/.registration_token:ro" \
  -v "${BACKUP_PATH}:/backup" \
  -e CONDUWUIT_SERVER_NAME="$SERVER_DOMAIN" \
  -e CONDUWUIT_DATABASE_PATH="/var/lib/conduwuit/conduwuit.db" \
  -e CONDUWUIT_DATABASE_BACKUP_PATH="/backup" \
  -e CONDUWUIT_ALLOW_REGISTRATION=true \
  -e CONDUWUIT_REGISTRATION_TOKEN_FILE="/.registration_token" \
  -e CONDUWUIT_PORT=$CONTAINER_PORT \
  -e CONDUWUIT_ADDRESS="0.0.0.0" \
  -e CONDUWUIT_NEW_USER_DISPLAYNAME_SUFFIX="" \
  -e CONDUWUIT_ALLOW_PUBLIC_ROOM_DIRECTORY_OVER_FEDERATION=true \
  -e CONDUWUIT_ALLOW_PUBLIC_ROOM_DIRECTORY_WITHOUT_AUTH=true \
  -e CONDUWUIT_ALLOW_FEDERATION=true \
  -e CONDUWUIT_AUTO_JOIN_ROOMS="$AUTO_JOIN_ROOMS" \
  -e CONDUWUIT_DB_CACHE_CAPACITY_MB=$DB_READ_CACHE_MB \
  -e CONDUWUIT_DB_WRITE_BUFFER_CAPACITY_MB=$DB_WRITE_BUFFER_MB \
  -e CONDUWUIT_CACHE_CAPACITY_MODIFIER=$CACHE_MODIFIER \
  -e CONDUWUIT_DB_POOL_WORKERS=$DB_POOL_WORKERS \
  -e CONDUWUIT_STREAM_WIDTH_SCALE=$STREAM_WIDTH_SCALE \
  -e CONDUWUIT_STREAM_AMPLIFICATION=$STREAM_AMPLIFICATION \
  -e CONDUWUIT_MAX_REQUEST_SIZE=$MAX_REQUEST_SIZE \
  -e CONDUWUIT_BLURHASH_MAX_RAW_SIZE=$BLURHASH_MAX_SIZE \
  --name $CONTAINER_NAME \
  --restart unless-stopped \
  $CONTAINER_IMAGE

if [ $? -ne 0 ]; then
    log "ERROR: Failed to create new conduwuit container"
    exit 1
fi

log "Successfully recreated conduwuit container with new token"
