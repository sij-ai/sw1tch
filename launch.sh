#!/bin/bash

# File paths for sw1tch and tuwunel integration
BASE_PATH="/home/sij/hand_of_morpheus/sw1tch"        # Base directory for sw1tch package
TOKEN_FILE="$BASE_PATH/data/.registration_token"     # File storing the current registration token
LOG_FILE="$BASE_PATH/logs/token_refresh.log"         # Log file for token refresh and script actions
BACKUP_PATH="/home/sij/tuwunel_backup"             # Directory for tuwunel backups
ENV_FILE="$BASE_PATH/config/tuwunel.env"             # Environment file for tuwunel settings
REPO_PATH="$HOME/workshop/tuwunel"                   # Path to tuwunel source repository
CONFIG_FILE="$BASE_PATH/config/config.yaml"          # sw1tch configuration file

# Static container settings for tuwunel
CONTAINER_NAME="tuwunel"                             # Name of the tuwunel Docker container
CONTAINER_IMAGE="tuwunel:custom"                     # Custom Docker image tag for tuwunel

# Flags to control script behavior (default to false)
REFRESH_TOKEN=false  # --refresh-token: Generates a new registration token
SUPER_ADMIN=false    # --super-admin: Sets an emergency password for @conduit user
UPDATE=false         # --update: Pulls and rebuilds the tuwunel Docker image
FORCE_RESTART=false  # --force-restart: Forces a restart of the sw1tch service

# Function to log messages with a timestamp to both file and terminal
log() {
    local message="$(date --iso-8601=seconds) $1"
    echo "$message" >> "$LOG_FILE"
    echo "$message"
}

# Function to refresh the registration token
# Triggered by --refresh-token flag
# Generates a new 6-character hex token and writes it to TOKEN_FILE
refresh_token() {
    NEW_TOKEN=$(openssl rand -hex 3)  # Short token for simplicity
    echo -n "$NEW_TOKEN" > "$TOKEN_FILE"
    if [ $? -ne 0 ]; then
        log "ERROR: Failed to write new token to $TOKEN_FILE"
        exit 1
    fi
    log "Generated new registration token: $NEW_TOKEN"
}

# Function to update the tuwunel Docker image
# Triggered by --update flag
# Pulls latest tuwunel source, builds it with Nix, and tags the Docker image
update_docker_image() {
    log "Updating tuwunel Docker image..."
    cd "$REPO_PATH" || {
        log "ERROR: Failed to cd into $REPO_PATH"
        exit 1
    }
    git pull origin main || {
        log "ERROR: git pull failed"
        exit 1
    }
    nix build -L --extra-experimental-features "nix-command flakes" .#oci-image-x86_64-linux-musl-all-features || {
        log "ERROR: nix build failed"
        exit 1
    }
    IMAGE_TAR_PATH=$(readlink -f result)
    if [ ! -f "$IMAGE_TAR_PATH" ]; then
        log "ERROR: No image tarball found at $IMAGE_TAR_PATH"
        exit 1
    fi
    docker load < "$IMAGE_TAR_PATH" | awk '/Loaded image:/ { print $3 }' | xargs -I {} docker tag {} "$CONTAINER_IMAGE"
    if [ $? -ne 0 ]; then
        log "ERROR: Failed to load and tag Docker image"
        exit 1
    fi
    log "Docker image tagged as $CONTAINER_IMAGE"
}

# Function to restart the tuwunel container
# Always runs unless script exits earlier
# Stops and removes the existing container, then starts a new one with updated settings
restart_container() {
    docker stop "$CONTAINER_NAME" 2>/dev/null  # Silently stop if running
    docker rm "$CONTAINER_NAME" 2>/dev/null    # Silently remove if exists

    # Base Docker command with volume mounts and network settings
    DOCKER_CMD=(docker run -d
        -v "db:/var/lib/conduwuit/"            # Persistent tuwunel data
        -v "${TOKEN_FILE}:/.registration_token:ro"  # Mount token file read-only
        -v "${BACKUP_PATH}:/backup"            # Backup directory
        --network host                         # Use host networking
        --name "$CONTAINER_NAME"               # Container name
        --restart unless-stopped               # Restart policy
    )

    # Load environment variables from tuwunel.env
    if [ -f "$ENV_FILE" ]; then
        while IFS='=' read -r key value; do
            [[ -z "$key" || "$key" =~ ^# ]] && continue
            key=$(echo "$key" | xargs)
            value=$(echo "$value" | xargs)
            if [[ "$key" =~ ^CONDUWUIT_ ]]; then
                log "Adding env var: $key=$value"
                DOCKER_CMD+=(-e "$key=$value")
            fi
        done < "$ENV_FILE"
    else
        log "ERROR: Environment file $ENV_FILE not found"
        exit 1
    fi

    # Set detailed logging for debugging
    DOCKER_CMD+=(-e RUST_LOG="conduwuit=trace,reqwest=trace,hickory_proto=trace")

    # If --super-admin is set, generate and apply an emergency password for @conduit
    if [ "$SUPER_ADMIN" = true ]; then
        EMERGENCY_PASSWORD=$(openssl rand -hex 8)  # 16-character hex password
        log "Setting emergency password to: $EMERGENCY_PASSWORD"
        DOCKER_CMD+=(-e CONDUWUIT_EMERGENCY_PASSWORD="$EMERGENCY_PASSWORD")
    fi

    DOCKER_CMD+=("$CONTAINER_IMAGE")  # Append the image name

    log "Docker command: ${DOCKER_CMD[*]}"
    "${DOCKER_CMD[@]}"
    if [ $? -ne 0 ]; then
        log "ERROR: Failed to create new conduwuit container"
        exit 1
    fi

    log "Successfully recreated container \"$CONTAINER_NAME\" with image \"$CONTAINER_IMAGE\"."
    log " - Configuration loaded from $ENV_FILE"
    
    # Provide login instructions if --super-admin was used
    if [ "$SUPER_ADMIN" = true ]; then
        log "Use the following credentials to log in as the @conduit server user:"
        log "  Username: @conduit:we2.ee"
        log "  Password: $EMERGENCY_PASSWORD"
        log "Once logged in as @conduit:we2.ee, you can invite yourself to the admin room or run admin commands."
    fi
}

# Function to ensure the sw1tch registration service is running
# Always runs unless script exits earlier
# Checks port, restarts if --force-restart is set, or starts if not running
ensure_registration_service() {
    local pid_file="$BASE_PATH/data/registration.pid"
    local log_file="$BASE_PATH/logs/registration.log"

    touch "$log_file" || { log "ERROR: Cannot write to $log_file"; exit 1; }
    chmod 666 "$log_file"  # Ensure log file is writable by all (adjust as needed)

    REG_PORT=$(python3 -c "import yaml, sys; print(yaml.safe_load(open('$CONFIG_FILE')).get('port', 8000))")
    log "Registration service port from config: $REG_PORT"

    if [ "$FORCE_RESTART" = true ]; then
        # --force-restart: Kills any process on the port and starts sw1tch anew
        log "Force restart requested. Clearing any process listening on port $REG_PORT..."
        PIDS=$(lsof -ti tcp:"$REG_PORT")
        if [ -n "$PIDS" ]; then
            kill -9 $PIDS && log "Killed processes: $PIDS" || log "Failed to kill process(es) on port $REG_PORT"
        else
            log "No process found running on port $REG_PORT"
        fi
        rm -f "$pid_file"  # Clear old PID file
        log "Force starting registration service..."
        cd "$(dirname "$BASE_PATH")" || { log "ERROR: Cannot cd to $(dirname "$BASE_PATH")"; exit 1; }
        log "Running: nohup python3 -m sw1tch >> $log_file 2>&1 &"
        nohup python3 -m sw1tch >> "$log_file" 2>&1 &  # Run detached
        NEW_PID=$!
        sleep 2  # Wait for process to start
        if ps -p "$NEW_PID" > /dev/null; then
            echo "$NEW_PID" > "$pid_file"
            log "Started registration service with PID $NEW_PID"
            sudo lsof -i :"$REG_PORT" || log "WARNING: No process on port $REG_PORT after start"
        else
            log "ERROR: Process $NEW_PID did not start or exited immediately"
            cat "$log_file" >> "$LOG_FILE"  # Append service logs for debugging
        fi
    else
        # Normal mode: Start sw1tch only if not already running
        EXISTING_PIDS=$(lsof -ti tcp:"$REG_PORT")
        if [ -n "$EXISTING_PIDS" ]; then
            log "Registration service already running on port $REG_PORT with PID(s): $EXISTING_PIDS"
        else
            log "Registration service not running on port $REG_PORT, starting..."
            cd "$(dirname "$BASE_PATH")" || { log "ERROR: Cannot cd to $(dirname "$BASE_PATH")"; exit 1; }
            log "Running: nohup python3 -m sw1tch >> $log_file 2>&1 &"
            nohup python3 -m sw1tch >> "$log_file" 2>&1 &
            NEW_PID=$!
            sleep 2
            if ps -p "$NEW_PID" > /dev/null; then
                echo "$NEW_PID" > "$pid_file"
                log "Started registration service with PID $NEW_PID"
                sudo lsof -i :"$REG_PORT" || log "WARNING: No process on port $REG_PORT after start"
            else
                log "ERROR: Process $NEW_PID did not start or exited immediately"
                cat "$log_file" >> "$LOG_FILE"
            fi
        fi
    fi
}

# Parse command-line flags to determine script actions
while [[ $# -gt 0 ]]; do
    case "$1" in
        # --refresh-token: Regenerate the registration token
        # Use: When you need a new token (e.g., daily via cron or after a security concern)
        --refresh-token) REFRESH_TOKEN=true; shift;;
        
        # --super-admin: Set an emergency password for @conduit user in tuwunel
        # Use: For initial setup or if admin access is lost; logs credentials for manual login
        --super-admin) SUPER_ADMIN=true; shift;;
        
        # --update: Update the tuwunel Docker image from source
        # Use: To apply the latest tuwunel changes (e.g., weekly via cron)
        --update) UPDATE=true; shift;;
        
        # --force-restart: Forcefully restart the sw1tch service, killing any existing process
        # Use: After updates, config changes, or if the service is unresponsive
        --force-restart) FORCE_RESTART=true; shift;;
        
        *) log "ERROR: Unknown option: $1"; echo "Usage: $0 [--refresh-token] [--super-admin] [--update] [--force-restart]"; exit 1;;
    esac
done

# Execute functions based on flags (order matters: update image before restarting)
if [ "$UPDATE" = true ]; then update_docker_image; fi
if [ "$REFRESH_TOKEN" = true ]; then refresh_token; fi
restart_container  # Always restart container to apply token or image changes
ensure_registration_service  # Always ensure sw1tch is running

exit 0
