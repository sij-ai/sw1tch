#!/bin/bash

# File paths
BASE_PATH="/home/sij/hand_of_morpheus"
TOKEN_FILE="$BASE_PATH/.registration_token"
LOG_FILE="$BASE_PATH/logs/token_refresh.log"
BACKUP_PATH="/home/sij/conduwuit_backup"
ENV_FILE="$BASE_PATH/config/conduwuit.env"
REPO_PATH="$HOME/workshop/conduwuit"
CONFIG_FILE="$BASE_PATH/config/config.yaml"

# Static container settings
CONTAINER_NAME="conduwuit"
CONTAINER_IMAGE="conduwuit:custom"

# Flags
REFRESH_TOKEN=false
SUPER_ADMIN=false
UPDATE=false
FORCE_RESTART=false

# Function to log with a timestamp to both file and terminal
log() {
    local message="$(date --iso-8601=seconds) $1"
    echo "$message" >> "$LOG_FILE"  # Write to log file
    echo "$message"                 # Print to terminal
}

# Function to refresh the registration token
refresh_token() {
    NEW_TOKEN=$(openssl rand -hex 3)
    echo -n "$NEW_TOKEN" > "$TOKEN_FILE"
    if [ $? -ne 0 ]; then
        log "ERROR: Failed to write new token to $TOKEN_FILE"
        exit 1
    fi
    log "Generated new registration token: $NEW_TOKEN"
}

# Function to update the Docker image
update_docker_image() {
    log "Updating Conduwuit Docker image..."

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

# Function to restart the container
restart_container() {
    docker stop "$CONTAINER_NAME" 2>/dev/null
    docker rm "$CONTAINER_NAME" 2>/dev/null

    DOCKER_CMD=(docker run -d
        -v "db:/var/lib/conduwuit/"
        -v "${TOKEN_FILE}:/.registration_token:ro"
        -v "${BACKUP_PATH}:/backup"
        --network host
        --name "$CONTAINER_NAME"
        --restart unless-stopped
    )

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

    DOCKER_CMD+=(-e RUST_LOG="conduwuit=trace,reqwest=trace,hickory_proto=trace")

    if [ "$SUPER_ADMIN" = true ]; then
        EMERGENCY_PASSWORD=$(openssl rand -hex 8)
        log "Setting emergency password to: $EMERGENCY_PASSWORD"
        DOCKER_CMD+=(-e CONDUWUIT_EMERGENCY_PASSWORD="$EMERGENCY_PASSWORD")
    fi

    DOCKER_CMD+=("$CONTAINER_IMAGE")

    log "Docker command: ${DOCKER_CMD[*]}"
    "${DOCKER_CMD[@]}"
    if [ $? -ne 0 ]; then
        log "ERROR: Failed to create new conduwuit container"
        exit 1
    fi

    log "Successfully recreated container \"$CONTAINER_NAME\" with image \"$CONTAINER_IMAGE\"."
    log " - Configuration loaded from $ENV_FILE"
    
    if [ "$SUPER_ADMIN" = true ]; then
        log "Use the following credentials to log in as the @conduit server user:"
        log "  Username: @conduit:we2.ee"
        log "  Password: $EMERGENCY_PASSWORD"
        log "Once logged in as @conduit:we2.ee, you can invite yourself to the admin room or run admin commands."
    fi
}

# Function to ensure the registration service is running
ensure_registration_service() {
    local python_script="$BASE_PATH/registration.py"
    local pid_file="$BASE_PATH/data/registration.pid"
    local log_file="$BASE_PATH/logs/registration.log"

    if [ ! -f "$python_script" ]; then
        log "ERROR: Python script $python_script not found"
        exit 1
    fi

    REG_PORT=$(python3 -c "import yaml, sys; print(yaml.safe_load(open('$CONFIG_FILE')).get('port', 8000))")
    log "Registration service port from config: $REG_PORT"
    
    if [ "$FORCE_RESTART" = true ]; then
        log "Force restart requested. Clearing any process listening on port $REG_PORT..."
        PIDS=$(lsof -ti tcp:"$REG_PORT")
        if [ -n "$PIDS" ]; then
            kill -9 $PIDS && log "Killed processes: $PIDS" || log "Failed to kill process(es) on port $REG_PORT"
        else
            log "No process found running on port $REG_PORT"
        fi
        rm -f "$pid_file"
        log "Force starting registration service..."
        python3 "$python_script" >> "$log_file" 2>&1 &
        NEW_PID=$!
        echo "$NEW_PID" > "$pid_file"
        log "Started registration service with PID $NEW_PID"
    else
        EXISTING_PIDS=$(lsof -ti tcp:"$REG_PORT")
        if [ -n "$EXISTING_PIDS" ]; then
            log "Registration service already running on port $REG_PORT with PID(s): $EXISTING_PIDS"
        else
            log "Registration service not running on port $REG_PORT, starting..."
            python3 "$python_script" >> "$log_file" 2>&1 &
            NEW_PID=$!
            echo "$NEW_PID" > "$pid_file"
            log "Started registration service with PID $NEW_PID"
        fi
    fi
}

# Parse command-line flags
while [[ $# -gt 0 ]]; do
    case "$1" in
        --refresh-token)
            REFRESH_TOKEN=true
            shift
            ;;
        --super-admin)
            SUPER_ADMIN=true
            shift
            ;;
        --update)
            UPDATE=true
            shift
            ;;
        --force-restart)
            FORCE_RESTART=true
            shift
            ;;
        *)
            log "ERROR: Unknown option: $1"
            echo "Usage: $0 [--refresh-token] [--super-admin] [--update] [--force-restart]"
            exit 1
            ;;
    esac
done

# Execute based on flags
if [ "$UPDATE" = true ]; then
    update_docker_image
fi
if [ "$REFRESH_TOKEN" = true ]; then
    refresh_token
fi
restart_container
ensure_registration_service

exit 0
