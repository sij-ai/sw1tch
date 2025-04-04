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

# File paths
CONFIG_FILE = "config.yaml"
OUTPUT_FILE = "canary.txt"
TEMP_MESSAGE_FILE = "temp_canary_message.txt"

def load_config():
    """Load configuration from YAML file."""
    try:
        if not os.path.exists(CONFIG_FILE):
            print(f"Error: Configuration file '{CONFIG_FILE}' not found.")
            sys.exit(1)
        with open(CONFIG_FILE, 'r') as file:
            config = yaml.safe_load(file)
        required = [('gpg', 'key_id'), ('canary', 'organization'), ('canary', 'attestations')]
        for section, field in required:
            if section not in config or field not in config[section]:
                print(f"Error: Missing required field '{section}.{field}' in config.")
                sys.exit(1)
        return config
    except Exception as e:
        print(f"Error loading configuration: {e}")
        sys.exit(1)

def get_current_date():
    """Return the current date in YYYY-MM-DD format."""
    return datetime.datetime.now().strftime("%Y-%m-%d")

def get_nist_time():
    """Get the current time from NIST or fallback servers."""
    session = requests.Session()
    retry_strategy = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    endpoints = [
        "https://timeapi.io/api/Time/current/zone?timeZone=UTC",
        "https://worldtimeapi.org/api/timezone/UTC",
    ]
    for url in endpoints:
        try:
            response = session.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            if "dateTime" in data:
                return data["dateTime"] + " UTC"
            elif "utc_datetime" in data:
                return data["utc_datetime"] + " UTC"
            print(f"Warning: Unexpected response format from {url}")
        except requests.exceptions.RequestException as e:
            print(f"Error fetching NIST time from {url}: {e}")
    return None

def get_rss_headline(config):
    """Get the latest headline and link from the configured RSS feed."""
    try:
        rss_config = config.get('rss', {})
        rss_url = rss_config.get('url', 'https://www.democracynow.org/democracynow.rss')
        feed = feedparser.parse(rss_url)
        if feed.entries and len(feed.entries) > 0:
            entry = feed.entries[0]
            return {"title": entry.title, "link": entry.link}
        print(f"No entries found in RSS feed: {rss_url}")
        return None
    except Exception as e:
        print(f"Error fetching RSS headline: {e}")
        return None

def get_bitcoin_latest_block():
    """Get the latest Bitcoin block hash and number."""
    try:
        response = requests.get("https://blockchain.info/latestblock", timeout=10)
        if response.status_code == 200:
            data = response.json()
            block_response = requests.get(f"https://blockchain.info/rawblock/{data['hash']}", timeout=10)
            if block_response.status_code == 200:
                block_data = block_response.json()
                hash_str = data["hash"].lstrip("0") or "0"
                return {
                    "height": data["height"],
                    "hash": hash_str,
                    "time": datetime.datetime.fromtimestamp(block_data["time"]).strftime("%Y-%m-%d %H:%M:%S UTC")
                }
        print(f"Error fetching Bitcoin block: HTTP {response.status_code}")
        return None
    except Exception as e:
        print(f"Error fetching Bitcoin block data: {e}")
        return None

def collect_attestations(config):
    """Prompt user for each attestation from config."""
    selected_attestations = []
    org = config['canary']['organization']
    print("\nPlease confirm each attestation separately:")
    for i, attestation in enumerate(config['canary']['attestations'], 1):
        while True:
            response = input(f"Confirm: '{org} {attestation}' (y/n): ").lower()
            if response in ['y', 'n']:
                break
            print("Please answer 'y' or 'n'.")
        if response == 'y':
            selected_attestations.append(attestation)
    return selected_attestations

def get_optional_note():
    """Prompt user for an optional note."""
    note = input("\nAdd an optional note (press Enter to skip): ").strip()
    return note if note else None

def create_warrant_canary_message(config):
    """Create the warrant canary message with updated formatting."""
    current_date = get_current_date()
    nist_time = get_nist_time()
    rss_data = get_rss_headline(config)
    bitcoin_block = get_bitcoin_latest_block()
    
    if not all([nist_time, rss_data, bitcoin_block]):
        missing = []
        if not nist_time: missing.append("NIST time")
        if not rss_data: missing.append(f"{config['rss'].get('name', 'RSS')} headline")
        if not bitcoin_block: missing.append("Bitcoin block data")
        print(f"Error: Could not fetch: {', '.join(missing)}")
        return None
    
    attestations = collect_attestations(config)
    if not attestations:
        proceed = input("No attestations confirmed. Proceed anyway? (y/n): ").lower()
        if proceed != 'y':
            print("Operation cancelled")
            return None
    
    note = get_optional_note()
    org = config['canary']['organization']
    admin_name = config['canary'].get('admin_name', 'Admin')
    admin_title = config['canary'].get('admin_title', 'administrator')
    rss_name = config['rss'].get('name', 'RSS Feed')
    
    # No leading \n; GPG adds one blank line after Hash: SHA512
    message = f"{org} Warrant Canary · {nist_time}\n"
    message += f"I, {admin_name}, the {admin_title} of {org}, state this {datetime.datetime.now().strftime('%dth day of %B, %Y')}:\n"
    for i, attestation in enumerate(attestations, 1):
        message += f"  {i}. {org} {attestation}\n"
    
    if note:
        message += f"\nNOTE: {note}\n"
    
    message += "\nDatestamp Proof:\n"
    message += f"  Daily News:  \"{rss_data['title']}\"\n"
    message += f"  Source URL:  {rss_data['link']}\n"
    message += f"  BTC block:   #{bitcoin_block['height']}, {bitcoin_block['time']}\n"
    message += f"  Block hash:  {bitcoin_block['hash']}\n"
    
    return message.rstrip() + "\n"  # Single newline before signature

def sign_with_gpg(message, gpg_key_id):
    """Sign the warrant canary message with GPG, ensuring no extra newline after signature header."""
    try:
        with open(TEMP_MESSAGE_FILE, "w", newline='\n') as f:  # Unix line endings
            f.write(message)
        cmd = ["gpg", "--clearsign", "--default-key", gpg_key_id, TEMP_MESSAGE_FILE]
        subprocess.run(cmd, check=True)
        with open(f"{TEMP_MESSAGE_FILE}.asc", "r") as f:
            signed_message = f.read()
        os.remove(TEMP_MESSAGE_FILE)
        os.remove(f"{TEMP_MESSAGE_FILE}.asc")
        # Fix GPG's extra newline after -----BEGIN PGP SIGNATURE-----
        lines = signed_message.splitlines()
        signature_idx = next(i for i, line in enumerate(lines) if line == "-----BEGIN PGP SIGNATURE-----")
        if lines[signature_idx + 1] == "":
            lines.pop(signature_idx + 1)  # Remove blank line
        signed_message = "\n".join(lines)
        return signed_message
    except subprocess.CalledProcessError as e:
        print(f"GPG signing error: {e}")
        return None
    except Exception as e:
        print(f"Error during GPG signing: {e}")
        return None

def save_warrant_canary(signed_message):
    """Save the signed warrant canary to a file."""
    try:
        with open(OUTPUT_FILE, "w") as f:
            f.write(signed_message)
        print(f"Warrant canary saved to {OUTPUT_FILE}")
        return True
    except Exception as e:
        print(f"Error saving warrant canary: {e}")
        return False

async def post_to_matrix(config, signed_message):
    """Post the signed warrant canary to Matrix room."""
    if not config.get('matrix', {}).get('enabled', False):
        print("Matrix posting is disabled in config")
        return False
    try:
        from nio import AsyncClient, LoginResponse
        matrix = config['matrix']
        client = AsyncClient(matrix['homeserver'], matrix['username'])
        await client.login(matrix['password'])
        
        full_message = (
            f"This is the {config['canary']['organization']} Warrant Canary, signed with GPG for authenticity. "
            "Copy the code block below to verify with `gpg --verify`:\n\n"
            f"```\n{signed_message}\n```"
        )
        
        content = {
            "msgtype": "m.text",
            "body": full_message,
            "format": "org.matrix.custom.html",
            "formatted_body": (
                f"This is the {config['canary']['organization']} Warrant Canary, signed with GPG for authenticity. "
                "Copy the code block below to verify with <code>gpg --verify</code>:<br><br>"
                f"<pre>{signed_message}</pre>"
            )
        }
        await client.room_send(matrix['room_id'], "m.room.message", content)
        await client.logout()
        await client.close()
        print("Posted to Matrix successfully")
        return True
    except Exception as e:
        print(f"Error posting to Matrix: {e}")
        return False

def main():
    print("Generating warrant canary...")
    config = load_config()
    message = create_warrant_canary_message(config)
    if not message:
        print("Failed to create message")
        sys.exit(1)
    
    print("\nWarrant Canary Preview:")
    print("-" * 50)
    print(message)
    print("-" * 50)
    
    if input("\nSign with GPG? (y/n): ").lower() != 'y':
        print("Operation cancelled")
        sys.exit(0)
    
    signed_message = sign_with_gpg(message, config['gpg']['key_id'])
    if not signed_message:
        print("Failed to sign message")
        sys.exit(1)
    
    if save_warrant_canary(signed_message) and config.get('matrix', {}).get('enabled', False):
        if input("Post to Matrix? (y/n): ").lower() == 'y':
            asyncio.run(post_to_matrix(config, signed_message))

if __name__ == "__main__":
    main()
