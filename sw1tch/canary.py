#!/usr/bin/env python3

import yaml
import requests
import feedparser
import datetime
import subprocess
import os
import sys
import asyncio
import ntplib
import calendar
import time
import email.utils
from pathlib import Path
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from datetime import timezone # For timezone-aware datetime objects

# --- Configuration ---
# File paths relative to the script's parent directory (sw1tch/)
TOP_DIR = Path(__file__).parent.parent
BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config" / "config.yaml"
ATTESTATIONS_FILE = BASE_DIR / "config" / "attestations.txt"
OUTPUT_FILE = TOP_DIR / "canary.txt"
# OUTPUT_FILE = BASE_DIR / "data" / "canary.txt"
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
    """Fetches the current UTC time from NTP servers with fallback to system time."""
    # List of reliable NTP servers to try
    ntp_servers = [
        'pool.ntp.org',
        'time.nist.gov',
        'time.google.com',
        '0.pool.ntp.org',
        '1.pool.ntp.org'
    ]
    
    for server in ntp_servers:
        try:
            print(f"Fetching time from NTP server {server}...")
            ntp_client = ntplib.NTPClient()
            response = ntp_client.request(server, version=3, timeout=10)
            
            # Convert NTP timestamp to UTC datetime
            # NTP epoch is 1900-01-01, Unix epoch is 1970-01-01
            # response.tx_time is seconds since NTP epoch
            utc_time = datetime.datetime.fromtimestamp(response.tx_time, timezone.utc)
            formatted_time = utc_time.strftime("%Y-%m-%d %H:%M:%S UTC")
            
            print(f"Successfully fetched NTP time: {formatted_time}")
            return formatted_time
            
        except ntplib.NTPException as e:
            print(f"NTP error from {server}: {e}")
        except Exception as e:
            print(f"Error fetching time from NTP server {server}: {e}")
    
    print("Error: Could not fetch time from any NTP source. Falling back to system time.")
    return datetime.datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

def get_entry_date(entry):
    """Extract the best available date from an RSS entry, returning Unix timestamp or None."""
    # Try normalized struct_time fields first (UTC-safe)
    for field in ('published_parsed', 'updated_parsed', 'issued_parsed', 'created_parsed'):
        parsed_time = entry.get(field)
        if parsed_time:
            try:
                return calendar.timegm(parsed_time)  # UTC-safe conversion
            except (TypeError, ValueError):
                continue
    
    # Fall back to date strings
    for field in ('published', 'updated', 'issued', 'created', 'dc_date', 'pubDate'):
        date_str = entry.get(field)
        if not date_str:
            continue
            
        # Try RFC 2822 format (e.g., "Fri, 22 Aug 2025 18:02:17 GMT")
        try:
            dt = email.utils.parsedate_to_datetime(date_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except (ValueError, TypeError):
            pass
            
        # Try ISO 8601 format (e.g., "2025-08-22T18:02:17Z")
        try:
            # Handle Z suffix and other common variations
            normalized = date_str.replace('Z', '+00:00')
            dt = datetime.datetime.fromisoformat(normalized)
            return dt.timestamp()
        except (ValueError, TypeError):
            pass
    
    return None

def get_rss_headline(config):
    """Fetches the most recent headline and link from the configured RSS feed."""
    try:
        # Safely get RSS config, providing defaults
        rss_config = config.get('canary', {}).get('rss', {})
        rss_url = rss_config.get('url', 'https://www.theguardian.com/world/rss')
        rss_name = rss_config.get('name', 'The Guardian')
        print(f"Fetching {rss_name} headline from {rss_url}...")
        
        feed = feedparser.parse(rss_url)
        if not feed.entries:
            print(f"No entries found in RSS feed: {rss_url}")
            return None

        # Try to find the most recent entry by date
        entries_with_dates = []
        entries_without_dates = []
        
        for entry in feed.entries:
            entry_date = get_entry_date(entry)
            if entry_date is not None:
                entries_with_dates.append((entry, entry_date))
            else:
                entries_without_dates.append(entry)
        
        # Choose the most recent entry if we have dated entries
        if entries_with_dates:
            # Sort by timestamp (most recent first) and take the first
            entries_with_dates.sort(key=lambda x: x[1], reverse=True)
            selected_entry = entries_with_dates[0][0]
            selected_date = datetime.datetime.fromtimestamp(entries_with_dates[0][1], timezone.utc)
            print(f"Selected most recent entry from {selected_date.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        else:
            # Fall back to first entry in feed order
            selected_entry = feed.entries[0]
            print(f"No parseable dates found, using first entry in feed order")
        
        return {
            "title": selected_entry.get("title", "Untitled"),
            "link": selected_entry.get("link", "")
        }
        
    except Exception as e:
        print(f"Error fetching RSS headline: {e}")
        return None

def get_monero_latest_block():
    """Fetches the latest Monero block using public RPC nodes with fallback."""
    # List of public Monero RPC nodes to try
    rpc_nodes = [
        "http://node.community.rino.io:18081/json_rpc",
        "http://node.sethforprivacy.com:18089/json_rpc",
        "http://xmr.fail:18081/json_rpc",
        "http://nodes.hashvault.pro:18081/json_rpc"
    ]
    
    rpc_payload = {
        "jsonrpc": "2.0",
        "id": "0",
        "method": "get_last_block_header"
    }
    
    for node_url in rpc_nodes:
        try:
            print(f"Fetching Monero block from {node_url}...")
            response = requests.post(
                node_url, 
                json=rpc_payload,
                headers={'Content-Type': 'application/json'},
                timeout=15
            )
            response.raise_for_status()
            data = response.json()
            
            # Validate response structure
            if 'result' not in data or 'block_header' not in data['result']:
                print(f"Warning: Unexpected response format from {node_url}")
                continue
            
            block_header = data['result']['block_header']
            
            # Validate required fields
            if not all(k in block_header for k in ['height', 'hash', 'timestamp']):
                print(f"Warning: Missing required fields in block header from {node_url}")
                continue
            
            height = block_header['height']
            block_hash = block_header['hash']
            timestamp = block_header['timestamp']
            
            # Convert timestamp to UTC string
            timestamp_utc = datetime.datetime.fromtimestamp(
                timestamp, 
                timezone.utc
            ).strftime("%Y-%m-%d %H:%M:%S UTC")
            
            print(f"Successfully fetched Monero block: Height={height}, Hash={block_hash[:10]}...")
            
            return {
                "height": height,
                "hash": block_hash,
                "time": timestamp_utc
            }
            
        except requests.exceptions.RequestException as e:
            print(f"Error fetching from {node_url}: {e}")
            continue
        except (KeyError, ValueError) as e:
            print(f"Error parsing response from {node_url}: {e}")
            continue
    
    # If all nodes fail
    print("Error: Could not fetch Monero block data from any RPC node")
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

def get_optional_note():
    """Prompts user (if interactive) for an optional note."""
    note = input("\nAdd an optional note (press Enter to skip): ").strip()
    return note if note else None

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
    message += f"  News headline: {rss_data['title']}\n"
    message += f"  News URL:      {rss_data['link']}\n"
    message += f"  XMR block:     #{monero_block['height']}, {monero_block['time']}\n"
    message += f"  Block hash:    {monero_block['hash']}\n\n"

    return message

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
