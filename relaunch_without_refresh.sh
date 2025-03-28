#!/bin/bash

# File paths
BASE_PATH="/home/sij/hand_of_morpheus"
TOKEN_FILE="$BASE_PATH/.registration_token"
BACKUP_PATH="/home/sij/conduwuit_backup"

# Server/domain info
SERVER_DOMAIN="we2.ee"
HOST="127.0.0.1"
HOST_PORT=8448
CONTAINER_PORT=6167
CONTAINER_NAME="conduwuit"
CONTAINER_IMAGE="ghcr.io/girlbossceo/conduwuit:v0.5.0-rc3-b6e9dc3d98704c56027219d3775336910a0136c6"

# Keep max request size
MAX_REQUEST_SIZE=33554432  # 32MB

# Auto-join room configuration
AUTO_JOIN_ROOMS="[\"#pub:we2.ee\",\"#home:we2.ee\"]"
TRUSTED_SERVERS="[\"matrix.org\",\"envs.net\",\"tchncs.de\"]"
BANNED_SERVERS="[\"tzchat.org\"]"
NO_MEDIA_FROM="[\"bark.lgbt\",\"cutefunny.art\",\"tzchat.org\",\"nitro.chat\",\"lolispace.moe\",\"lolisho.chat\",\"midov.pl\"]"

# Recreate Conduwuit container
docker stop "$CONTAINER_NAME"
docker rm "$CONTAINER_NAME"

docker run -d \
  -p "${HOST}:${HOST_PORT}:${CONTAINER_PORT}" \
  -v "db:/var/lib/conduwuit/" \
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
  -e CONDUWUIT_ALLOW_PUBLIC_ROOM_DIRECTORY_WITHOUT_AUTH=false \
  -e CONDUWUIT_ALLOW_FEDERATION=true \
  -e CONDUWUIT_AUTO_JOIN_ROOMS="$AUTO_JOIN_ROOMS" \
  -e CONDUWUIT_MAX_REQUEST_SIZE=$MAX_REQUEST_SIZE \
  -e CONDUWUIT_LOG=debug \
  -e CONDUWUIT_LOG_SPAN_EVENTS=all \
  -e CONDUWUIT_LOG_COLORS=true \
  -e CONDUWUIT_TRUSTED_SERVERS=$TRUSTED_SERVERS \
  -e CONDUWUIT_PRUNE_MISSING_MEDIA=true \
  -e CONDUWUIT_ALLOW_LEGACY_MEDIA=false \
  -e CONDUWUIT_IP_RANGE_DENYLIST="[]" \
  -e CONDUWUIT_AUTO_DEACTIVATE_BANNED_ROOM_ATTEMPTS=true \
  -e CONDUWUIT_PREVENT_MEDIA_DOWNLOADS_FROM=$NO_MEDIA_FROM \
  -e CONDUWUIT_IP_LOOKUP_STRATEGY="1" \
  -e CONDUWUIT_QUERY_OVER_TCP_ONLY=true \
  -e CONDUWUIT_QUERY_ALL_NAMESERVERS=false \
  --name "$CONTAINER_NAME" \
  --restart unless-stopped \
  "$CONTAINER_IMAGE"
