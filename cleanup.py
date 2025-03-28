import os
import json
import yaml
import httpx
import logging
from typing import List, Dict
from datetime import datetime, timedelta

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load paths and config
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")
REGISTRATIONS_PATH = os.path.join(BASE_DIR, "registrations.json")

def load_config() -> dict:
    """Load configuration from yaml file."""
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)

def load_registrations() -> List[Dict]:
    """Load current registrations from JSON file."""
    try:
        with open(REGISTRATIONS_PATH, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def save_registrations(registrations: List[Dict]):
    """Save updated registrations back to JSON file."""
    with open(REGISTRATIONS_PATH, "w") as f:
        json.dump(registrations, f, indent=2)

async def check_username_exists(username: str, homeserver: str) -> bool:
    """
    Check if a username exists on the Matrix server.
    Returns True if the username exists, False otherwise.
    """
    url = f"https://{homeserver}/_matrix/client/v3/register/available?username={username}"
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, timeout=5)
            if response.status_code == 200:
                # 200 OK with available=true means username exists
                return response.json().get("available", False)
            elif response.status_code == 400:
                # 400 Bad Request means username is not available
                return False
        except httpx.RequestError as ex:
            logger.error(f"Error checking username {username}: {ex}")
            return False
    return False

async def cleanup_registrations(min_age_hours: int = 24):
    """
    Clean up registrations by removing entries for usernames that don't exist on the server,
    but only if they're older than min_age_hours.
    
    Never removes entries for existing Matrix users regardless of age.
    """
    config = load_config()
    registrations = load_registrations()
    
    if not registrations:
        logger.info("No registrations found to clean up")
        return

    logger.info(f"Starting cleanup of {len(registrations)} registrations")
    logger.info(f"Will only remove non-existent users registered more than {min_age_hours} hours ago")
    
    # Track which entries to keep
    entries_to_keep = []
    removed_count = 0
    too_new_count = 0
    exists_count = 0

    current_time = datetime.utcnow()

    for entry in registrations:
        username = entry["requested_name"]
        reg_date = datetime.fromisoformat(entry["datetime"])
        age = current_time - reg_date
        
        # First check if the user exists on Matrix
        exists = await check_username_exists(username, config["homeserver"])
        
        if exists:
            # Always keep entries for existing Matrix users
            entries_to_keep.append(entry)
            exists_count += 1
            logger.info(f"Keeping registration for existing user: {username}")
            continue
            
        # For non-existent users, check if they're old enough to remove
        if age < timedelta(hours=min_age_hours):
            # Keep young entries even if user doesn't exist yet
            entries_to_keep.append(entry)
            too_new_count += 1
            logger.info(f"Keeping recent registration: {username} (age: {age.total_seconds()/3600:.1f} hours)")
        else:
            # Remove old entries where user doesn't exist
            logger.info(f"Removing old registration: {username} (age: {age.total_seconds()/3600:.1f} hours)")
            removed_count += 1

    # Save updated registrations
    save_registrations(entries_to_keep)
    
    logger.info(f"Cleanup complete:")
    logger.info(f"- Kept {exists_count} entries for existing Matrix users")
    logger.info(f"- Kept {too_new_count} entries younger than {min_age_hours} hours")
    logger.info(f"- Removed {removed_count} old entries for non-existent users")
    logger.info(f"- Total remaining entries: {len(entries_to_keep)}")

if __name__ == "__main__":
    import asyncio
    import argparse
    
    parser = argparse.ArgumentParser(description="Clean up Matrix registration entries")
    parser.add_argument(
        "--min-age-hours", 
        type=int,
        default=24,
        help="Minimum age in hours before removing non-existent users (default: 24)"
    )
    
    args = parser.parse_args()
    
    asyncio.run(cleanup_registrations(args.min_age_hours))
