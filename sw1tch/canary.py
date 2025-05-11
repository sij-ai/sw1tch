#!/usr/bin/env python3

import yaml
import requests
import feedparser
import datetime
import subprocess
import os
import sys
import asyncio
from pathlib import Path
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from datetime import timezone # For timezone-aware datetime objects

# --- Configuration ---
# File paths relative to the script's parent directory (sw1tch/)
BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config" / "config.yaml"
ATTESTATIONS_FILE = BASE_DIR / "config" / "attestations.txt"
OUTPUT_FILE = BASE_DIR / "data" / "canary.txt"
TEMP_MESSAGE_FILE = BASE_DIR / "data" / "temp_canary_message.txt" # For GPG signing

# --- Core Functions ---

def load_config():
    """Loads configuration settings from the YAML file."""
    try:
        if not CONFIG_FILE.exists():
            print(f"Error: Configuration file '{CONFIG_FILE}' not found.")
            sys.exit(1)
        with open(CONFIG_FILE, 'r') as file:
            config = yaml.safe_load(file)

        # Validate essential non-Matrix config fields
        required = [
            ('canary', 'organization'),
            ('canary', 'gpg_key_id'),
        ]
        for path in required:
            current = config
            path_str = '.'.join(path)
            try:
                for key in path:
                    if key not in current: raise KeyError
                    current = current[key]
            except KeyError:
                 print(f"Error: Missing required field '{path_str}' in config.")
                 sys.exit(1)
            except TypeError:
                 print(f"Error: Invalid structure for '{path_str}' in config.")
                 sys.exit(1)

        # Basic validation of Matrix structure if present (full check done before posting)
        if 'canary' in config and 'credentials' in config['canary']:
            matrix_required_structure = [
                ('canary', 'credentials', 'username'),
                ('canary', 'credentials', 'password'),
                ('canary', 'room')
            ]
            for path in matrix_required_structure:
                current = config
                path_str = '.'.join(path)
                try:
                    for key in path:
                        if key not in current: break # Okay if missing, checked later
                        current = current[key]
                except TypeError:
                     print(f"Warning: Invalid structure for potential Matrix field '{path_str}'.")

        return config
    except Exception as e:
        print(f"Error loading configuration: {e}")
        sys.exit(1)

def load_attestations():
    """Loads attestation statements from the attestations text file."""
    try:
        if not ATTESTATIONS_FILE.exists():
            print(f"Error: Attestations file '{ATTESTATIONS_FILE}' not found.")
            sys.exit(1)
        with open(ATTESTATIONS_FILE, 'r') as f:
            # Return non-empty, stripped lines
            return [line.strip() for line in f if line.strip()]
    except Exception as e:
        print(f"Error loading attestations: {e}")
        sys.exit(1)

def get_nist_time():
    session = requests.Session()
    retry_strategy = Retry(total=5, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["GET"], raise_on_redirect=True, connect=3, read=3)
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    endpoints = [
        "http://worldclockapi.com/api/json/utc/now",  # Use HTTP due to HTTPS cert issue
        "https://timeapi.io/api/Time/current/zone?timeZone=UTC",
        "https://worldtimeapi.org/api/timezone/UTC"
    ]
    for url in endpoints:
        try:
            print(f"Fetching time from {url}...")
            response = session.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()

            if "currentDateTime" in data:
                dt_str = data["currentDateTime"]
            elif "dateTime" in data:
                dt_str = data["dateTime"]
            elif "utc_datetime" in data:
                dt_str = data["utc_datetime"]
            else:
                print(f"Warning: Unexpected response format from {url}")
                continue

            time_part = dt_str.split('.')[0].replace('T', ' ').replace('Z', '')
            if '+' not in time_part and '-' not in time_part.split(' ')[-1]:
                return f"{time_part} UTC"
            parts = time_part.split(' ')
            if '+' in parts[-1] or '-' in parts[-1]:
                return f"{' '.join(parts[:-1])} UTC"
            return f"{time_part} UTC"

        except requests.exceptions.RequestException as e:
            print(f"Error fetching NIST time from {url}: {e}")
        except Exception as e:
            print(f"Error processing time from {url}: {e}")

    print("Error: Could not fetch time from any source. Falling back to system time.")
    return datetime.datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

def old_get_nist_time():
    """Fetches the current UTC time from public time APIs with retries."""
    session = requests.Session()
    # Retry strategy for network issues
    retry_strategy = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    endpoints = [
        "https://timeapi.io/api/Time/current/zone?timeZone=UTC",
        "https://worldtimeapi.org/api/timezone/UTC",
    ]
    for url in endpoints:
        try:
            print(f"Fetching time from {url}...")
            response = session.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()

            if "dateTime" in data: dt_str = data["dateTime"]
            elif "utc_datetime" in data: dt_str = data["utc_datetime"]
            else:
                 print(f"Warning: Unexpected response format from {url}")
                 continue

            # Standardize format (YYYY-MM-DD HH:MM:SS UTC), remove microseconds/timezone info
            time_part = dt_str.split('.')[0].replace('T', ' ').replace('Z', '')
            # Add UTC if not already present from offset/Z
            if '+' not in time_part and '-' not in time_part.split(' ')[-1]:
                return f"{time_part} UTC"
            else: # Handle potential offsets, though UTC is requested
                 # Basic cleaning assuming offset is present
                 parts = time_part.split(' ')
                 if '+' in parts[-1] or '-' in parts[-1]:
                      return f"{' '.join(parts[:-1])} UTC"
                 else:
                      return f"{time_part} UTC"

        except requests.exceptions.RequestException as e:
            print(f"Error fetching NIST time from {url}: {e}")
        except Exception as e:
            print(f"Error processing time from {url}: {e}")

    print("Error: Could not fetch time from any source.")
    return None

def get_rss_headline(config):
    """Fetches the latest headline and link from the configured RSS feed."""
    try:
        # Safely get RSS config, providing defaults
        rss_config = config.get('canary', {}).get('rss', {})
        rss_url = rss_config.get('url', 'https://www.democracynow.org/democracynow.rss') # Default feed
        rss_name = rss_config.get('name', 'Democracy Now!') # Use specific default name
        print(f"Fetching {rss_name} headline from {rss_url}...")
        feed = feedparser.parse(rss_url)
        if feed.entries:
            entry = feed.entries[0]
            return {"title": entry.title, "link": entry.link}
        else:
             print(f"No entries found in RSS feed: {rss_url}")
             return None
    except Exception as e:
        print(f"Error fetching RSS headline: {e}")
        return None

def get_monero_latest_block():
    """Fetches the latest Monero block height, hash, and timestamp using public APIs."""
    # Use reliable source for height/timestamp
    stats_url = "https://localmonero.co/blocks/api/get_stats"
    # Use reliable source for block header (incl. hash) by height
    block_header_url_template = "https://moneroblocks.info/api/get_block_header/{}"

    try:
        # Step 1: Get latest height and timestamp
        print(f"Fetching Monero stats from {stats_url}...")
        stats_response = requests.get(stats_url, timeout=15)
        stats_response.raise_for_status()
        stats_data = stats_response.json()

        if not stats_data or 'height' not in stats_data or 'last_timestamp' not in stats_data:
            print(f"Error: Unexpected data format from Monero stats API ({stats_url})")
            return None

        height = stats_data['height']
        timestamp = stats_data['last_timestamp']
        # Use timezone-aware datetime object for UTC conversion
        timestamp_utc = datetime.datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        # Step 2: Get block header (including hash) using the height
        block_header_url = block_header_url_template.format(height)
        print(f"Fetching Monero block header from {block_header_url}...")
        header_response = requests.get(block_header_url, timeout=15)

        # Handle cases where the latest block isn't indexed yet (common timing issue)
        if header_response.status_code in [404, 500]:
            print(f"Warning: Block height {height} lookup failed on {block_header_url} (Status: {header_response.status_code}). Trying previous block ({height-1}).")
            height -= 1 # Fallback to previous block height
            block_header_url = block_header_url_template.format(height)
            print(f"Fetching Monero block header from {block_header_url}...")
            header_response = requests.get(block_header_url, timeout=15)
            header_response.raise_for_status() # Raise error if fallback also fails
        elif not header_response.ok: # Raise other non-2xx errors
             header_response.raise_for_status()

        header_data = header_response.json()

        # Validate expected structure from moneroblocks.info
        if not header_data or 'block_header' not in header_data or 'hash' not in header_data['block_header']:
            print(f"Error: Unexpected data format from Monero block header API ({block_header_url})")
            return None

        block_hash = header_data['block_header']['hash']

        print(f"Successfully fetched Monero block: Height={height}, Hash={block_hash[:10]}...")
        # Return potentially decremented height, the hash found, and the original latest timestamp
        return {
            "height": height,
            "hash": block_hash,
            "time": timestamp_utc
        }

    except requests.exceptions.RequestException as e:
        print(f"Error fetching Monero block data: {e}")
        return None
    except Exception as e:
        print(f"Error processing Monero block data: {e}")
        return None

def collect_attestations(config, is_interactive):
    """Loads attestations and confirms them with the user if interactive."""
    attestations = load_attestations()
    selected_attestations = []

    if is_interactive:
        org = config['canary']['organization']
        print("\nPlease confirm each attestation separately:")
        for i, attestation in enumerate(attestations, 1):
            while True:
                response = input(f"Confirm: '{org} {attestation}' (y/n): ").lower()
                if response in ['y', 'n']: break
                print("Please answer 'y' or 'n'.")
            if response == 'y':
                selected_attestations.append(attestation)
        if not selected_attestations:
            proceed = input("No attestations confirmed. Proceed anyway? (y/n): ").lower()
            if proceed != 'y':
                print("Operation cancelled")
                return None # Return None to signal cancellation
            else:
                 return [] # Return empty list if proceeding without confirmations
        else:
             return selected_attestations
    else:
        # Non-interactive: Assume all loaded attestations are confirmed
        selected_attestations = attestations
        if not selected_attestations:
             print("Warning: No attestations found in file. Proceeding without attestations.")
        else:
             print("Non-interactive mode: Including all attestations from file.")
        return selected_attestations

# --- Added back the missing function ---
def get_optional_note():
    """Prompts user (if interactive) for an optional note."""
    note = input("\nAdd an optional note (press Enter to skip): ").strip()
    return note if note else None
# --- End of added function ---

def create_warrant_canary_message(config, is_interactive):
    """Constructs the main body of the warrant canary message."""
    nist_time = get_nist_time()
    rss_data = get_rss_headline(config)
    monero_block = get_monero_latest_block()

    # Ensure all required data points were fetched
    if not all([nist_time, rss_data, monero_block]):
        missing = [item for item, data in [("NIST time", nist_time),
                                           ("RSS headline", rss_data),
                                           ("Monero block data", monero_block)] if not data]
        print(f"Error: Could not fetch necessary data: {', '.join(missing)}")
        return None

    # Handle attestations based on interactivity
    selected_attestations = collect_attestations(config, is_interactive)
    if selected_attestations is None: # Check if collect_attestations signaled cancellation
         return None

    # Get optional note only if interactive
    note = get_optional_note() if is_interactive else None

    # Get config details safely with defaults
    org = config.get('canary', {}).get('organization', 'Unknown Organization')
    admin_name = config.get('canary', {}).get('admin_name', 'Admin')
    admin_title = config.get('canary', {}).get('admin_title', 'administrator')

    # Format date with correct suffix (st, nd, rd, th)
    day = datetime.datetime.now().day
    if 11 <= day <= 13: suffix = 'th'
    else: suffixes = {1: 'st', 2: 'nd', 3: 'rd'}; suffix = suffixes.get(day % 10, 'th')
    current_date_str = datetime.datetime.now().strftime(f'%d{suffix} day of %B, %Y')

    # Build the message string
    message = f"{org} Warrant Canary · {nist_time}\n\n"
    message += f"I, {admin_name}, the {admin_title} of {org}, state this {current_date_str}:\n"
    for i, attestation in enumerate(selected_attestations, 1):
        message += f"  {i}. {org} {attestation}\n"

    if note:
        message += f"\nNOTE: {note}\n"

    message += "\nDatestamp Proof:\n"
    message += f"  Daily News:  \"{rss_data['title']}\"\n"
    message += f"  Source URL:  {rss_data['link']}\n"
    message += f"  XMR block:   #{monero_block['height']}, {monero_block['time']}\n"
    message += f"  Block hash:  {monero_block['hash']}\n" # Ensure this line ends with newline for GPG

    # Ensure single trailing newline before signing
    return message.rstrip() + "\n"

def sign_with_gpg(message, gpg_key_id):
    """Signs the message using GPG clearsign with the specified key ID."""
    if not gpg_key_id:
         print("Error: GPG Key ID is missing in config.")
         return None
    try:
        TEMP_MESSAGE_FILE.parent.mkdir(parents=True, exist_ok=True)

        print(f"Signing message with GPG key ID: {gpg_key_id}...")
        # Ensure input message ends with exactly one newline for GPG
        with open(TEMP_MESSAGE_FILE, "w", newline='\n', encoding='utf-8') as f:
            f.write(message.rstrip() + '\n')

        # Use --batch and --yes for non-interactive signing
        cmd = ["gpg", "--batch", "--yes", "--clearsign", "--default-key", gpg_key_id, str(TEMP_MESSAGE_FILE)]
        result = subprocess.run(cmd, check=True, capture_output=True, text=True, encoding='utf-8')

        # GPG might write to stdout or file depending on version/environment
        signed_message_path = Path(f"{TEMP_MESSAGE_FILE}.asc")
        if signed_message_path.exists():
            with open(signed_message_path, "r", encoding='utf-8') as f:
                signed_message = f.read()
            print(f"GPG signing successful (read from {signed_message_path}).")
        elif result.stdout:
             signed_message = result.stdout
             print("GPG signing successful (read from stdout).")
        else:
             print("Error: GPG signed message file not found and no stdout output.")
             if TEMP_MESSAGE_FILE.exists(): os.remove(TEMP_MESSAGE_FILE)
             return None

        # Clean up temporary files
        if TEMP_MESSAGE_FILE.exists(): os.remove(TEMP_MESSAGE_FILE)
        if signed_message_path.exists(): os.remove(signed_message_path)

        # Return the raw signed message
        return signed_message

    except subprocess.CalledProcessError as e:
        print(f"GPG signing error (Exit code: {e.returncode}): {e.stderr or e.stdout or 'No output'}")
        if TEMP_MESSAGE_FILE.exists(): os.remove(TEMP_MESSAGE_FILE)
        signed_message_path = Path(f"{TEMP_MESSAGE_FILE}.asc")
        if signed_message_path.exists(): os.remove(signed_message_path)
        return None
    except FileNotFoundError:
        print("Error: 'gpg' command not found. Is GnuPG installed and in your PATH?")
        return None
    except Exception as e:
        print(f"Error during GPG signing: {e}")
        if TEMP_MESSAGE_FILE.exists(): os.remove(TEMP_MESSAGE_FILE)
        signed_message_path = Path(f"{TEMP_MESSAGE_FILE}.asc")
        if signed_message_path.exists(): os.remove(signed_message_path)
        return None

def save_warrant_canary(signed_message):
    """Saves the signed warrant canary message to the output file."""
    try:
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        # Write exactly what GPG (or our adjusted version) gave us
        with open(OUTPUT_FILE, "w", newline='\n', encoding='utf-8') as f:
            f.write(signed_message)
        print(f"Warrant canary saved to {OUTPUT_FILE}")
        return True
    except Exception as e:
        print(f"Error saving warrant canary: {e}")
        return False

async def post_to_matrix(config, signed_message):
    """Posts the signed warrant canary message to the configured Matrix room."""
    # Validate Matrix configuration just before attempting to post
    if 'base_url' not in config:
        print("Error: 'base_url' missing in config. Cannot post to Matrix.")
        return False
    try:
        matrix_creds = config['canary']['credentials']
        room_id = config['canary']['room']
        org_name = config['canary']['organization']
        if not all([matrix_creds.get('username'), matrix_creds.get('password'), room_id, org_name]):
             print("Error: Missing Matrix credentials, room ID, or organization name in config.")
             return False
    except KeyError as e:
        print(f"Error: Missing structure for Matrix config (key: {e}). Cannot post.")
        return False

    # Ensure matrix-nio library is available
    try:
        from nio import AsyncClient, LoginError, RoomSendError
    except ImportError:
        print("Error: matrix-nio library not installed (pip install matrix-nio).")
        return False

    client = None
    try:
        client = AsyncClient(config['base_url'], matrix_creds['username'])
        print("Logging in to Matrix...")
        login_response = await client.login(matrix_creds['password'])
        if isinstance(login_response, LoginError):
             print(f"Matrix login failed: {login_response.message}")
             return False # Don't proceed if login fails
        print("Matrix login successful.")

        # Format message for Matrix (ensure code block formatting is correct)
        # The signed message already includes newlines, including the (now forced) one before signature
        full_message_body = (
            f"This is the {org_name} Warrant Canary, signed with GPG for authenticity. "
            "Copy the code block below to verify with `gpg --verify`:\n\n"
            f"```\n{signed_message.strip()}\n```" # Strip leading/trailing whitespace just in case
        )
        full_message_html = (
            f"<p>This is the {org_name} Warrant Canary, signed with GPG for authenticity. "
            "Copy the code block below to verify with <code>gpg --verify</code>:</p>"
            # Use html.escape or similar if needed, but pre should handle GPG block okay
            f"<pre><code>{signed_message.strip()}</code></pre>"
        )
        content = {
            "msgtype": "m.text",
            "body": full_message_body,
            "format": "org.matrix.custom.html",
            "formatted_body": full_message_html
        }

        print(f"Sending message to Matrix room: {room_id}")
        send_response = await client.room_send(room_id=room_id, message_type="m.room.message", content=content)

        if isinstance(send_response, RoomSendError):
            print(f"Error posting to Matrix room {room_id}: {send_response.message}")
            return False
        else:
            print("Posted to Matrix successfully.")
            return True

    except Exception as e:
        print(f"An unexpected error occurred during Matrix posting: {e}")
        return False
    finally:
        if client: # Ensure logout/close happens if client was created
            print("Logging out from Matrix...")
            await client.logout()
            await client.close()
            print("Matrix client closed.")

# --- Main Execution Logic ---

def main():
    """Main function to generate, sign, save, and optionally post the warrant canary."""
    print("Generating warrant canary...")
    config = load_config()

    # Detect if running interactively (e.g., in a terminal vs. cron)
    is_interactive = sys.stdout.isatty()
    if not is_interactive:
        print("Running in non-interactive mode.")

    # Create the message body
    message = create_warrant_canary_message(config, is_interactive)
    if not message:
        print("Failed to create message payload.")
        sys.exit(1)

    print("\n--- Warrant Canary Preview ---")
    print(message)
    print("----------------------------")

    # Get GPG key ID (checked in load_config, but check again for safety)
    gpg_key_id = config.get('canary', {}).get('gpg_key_id')
    if not gpg_key_id:
         print("Error: Missing 'gpg_key_id' in config under 'canary'. Cannot sign.")
         sys.exit(1)

    # Confirm GPG signing
    sign_confirm = 'n'
    if is_interactive:
        sign_confirm = input("\nSign with GPG? (y/n): ").lower()
    else:
        print("Non-interactive mode: Auto-confirming GPG signing.")
        sign_confirm = 'y' # Auto-sign in non-interactive mode

    if sign_confirm != 'y':
        print("Operation cancelled by user (GPG signing).")
        sys.exit(0)

    # Sign the message
    signed_message = sign_with_gpg(message, gpg_key_id)
    if not signed_message:
        print("Failed to sign message with GPG.")
        sys.exit(1) # Exit if signing failed

    # Save the signed message (potentially modified)
    if not save_warrant_canary(signed_message):
        print("Failed to save warrant canary file.")
        sys.exit(1) # Exit if saving failed

    # --- Optional Matrix Posting ---
    # Check if Matrix posting is feasible based on config
    can_post_matrix = all([
        'base_url' in config,
        config.get('canary', {}).get('credentials', {}).get('username'),
        config.get('canary', {}).get('credentials', {}).get('password'),
        config.get('canary', {}).get('room'),
        config.get('canary', {}).get('organization')
    ])
    # Check config for explicit auto-post flag for non-interactive runs
    auto_post = config.get('canary', {}).get('auto_post_matrix', False)

    post_confirm = 'n' # Default to no
    if is_interactive:
        if can_post_matrix:
            post_confirm = input("\nPost to Matrix? (y/n): ").lower()
        # Prompt even if config is bad, to inform user why it won't work
        elif input("\nPost to Matrix? (y/n): ").lower() == 'y':
             print("Cannot post to Matrix: Check 'base_url' and canary credentials/room/organization in config.")
    else: # Non-interactive
        if can_post_matrix and auto_post:
             print("Non-interactive mode: Auto-posting to Matrix is enabled.")
             post_confirm = 'y'
        elif can_post_matrix: # Auto-post is false or missing
             print("Non-interactive mode: Auto-posting to Matrix is disabled in config. Skipping.")
        else: # Config is incomplete
             print("Non-interactive mode: Cannot post to Matrix (incomplete config).")

    # Attempt posting if confirmed and possible
    if post_confirm == 'y' and can_post_matrix:
        print("Attempting to post to Matrix...")
        post_successful = asyncio.run(post_to_matrix(config, signed_message))
        if not post_successful:
             print("Matrix post failed. Check logs above.")
             # Allow script to finish successfully even if Matrix fails
    elif post_confirm == 'y' and not can_post_matrix:
        # Warning message already printed during prompt phase
        pass
    else:
        # Print skipping message unless already handled above
        already_handled = (post_confirm == 'y' and not can_post_matrix) or \
                          (not is_interactive and can_post_matrix and not auto_post) or \
                          (not is_interactive and not can_post_matrix)
        if not already_handled:
             print("Skipping Matrix post.")

    print("\nWarrant canary generation process complete.")


if __name__ == "__main__":
    main()
