#!/usr/bin/env python3

import yaml
import requests
import feedparser
import datetime
import subprocess
import json
import os
import sys
import asyncio
from time import sleep
from pathlib import Path

# File paths
CONFIG_FILE = "config.yaml"
OUTPUT_FILE = "canary.txt"
TEMP_MESSAGE_FILE = "temp_canary_message.txt"

# Possible attestations
ATTESTATIONS = [
    "We have not received any National Security Letters.",
    "We have not received any court orders under the Foreign Intelligence Surveillance Act.",
    "We have not received any gag orders that prevent us from stating we have received legal process.",
    "We have not been required to modify our systems to facilitate surveillance.",
    "We have not been subject to any searches or seizures of our servers."
]

def load_config():
    """Load configuration from YAML file."""
    try:
        if not os.path.exists(CONFIG_FILE):
            print(f"Error: Configuration file '{CONFIG_FILE}' not found.")
            print("Please create a configuration file with the following structure:")
            print("""
gpg:
  key_id: YOUR_GPG_KEY_ID
matrix:
  enabled: true
  homeserver: https://we2.ee
  username: @canary:we2.ee
  password: YOUR_PASSWORD
  room_id: !l7XTTF6tudReoEJEvr:we2.ee
            """)
            sys.exit(1)
            
        with open(CONFIG_FILE, 'r') as file:
            config = yaml.safe_load(file)
            
        # Check for required fields
        required_fields = [
            ('gpg', 'key_id')
        ]
        
        for section, field in required_fields:
            if section not in config or field not in config[section]:
                print(f"Error: Required configuration field '{section}.{field}' is missing.")
                sys.exit(1)
                
        return config
    except Exception as e:
        print(f"Error loading configuration: {e}")
        sys.exit(1)

def get_current_date():
    """Return the current date in YYYY-MM-DD format."""
    return datetime.datetime.now().strftime("%Y-%m-%d")

def get_nist_time():
    """Get the current time from NIST time server."""
    try:
        response = requests.get("https://timeapi.io/api/Time/current/zone?timeZone=UTC", timeout=10)
        if response.status_code == 200:
            time_data = response.json()
            return f"{time_data['dateTime']} UTC"
        else:
            print(f"Error fetching NIST time: HTTP {response.status_code}")
            return None
    except Exception as e:
        print(f"Error fetching NIST time: {e}")
        return None

def get_democracy_now_headline():
    """Get the latest headline from Democracy Now! RSS feed."""
    try:
        feed = feedparser.parse("https://www.democracynow.org/democracynow.rss")
        if feed.entries and len(feed.entries) > 0:
            return feed.entries[0].title
        else:
            print("No entries found in Democracy Now! RSS feed")
            return None
    except Exception as e:
        print(f"Error fetching Democracy Now! headline: {e}")
        return None

def get_bitcoin_latest_block():
    """Get the latest Bitcoin block hash and number."""
    try:
        response = requests.get("https://blockchain.info/latestblock", timeout=10)
        if response.status_code == 200:
            data = response.json()
            # Get block details
            block_response = requests.get(f"https://blockchain.info/rawblock/{data['hash']}", timeout=10)
            if block_response.status_code == 200:
                block_data = block_response.json()
                return {
                    "height": data["height"],
                    "hash": data["hash"],
                    "time": datetime.datetime.fromtimestamp(block_data["time"]).strftime("%Y-%m-%d %H:%M:%S UTC")
                }
        print(f"Error fetching Bitcoin block: HTTP {response.status_code}")
        return None
    except Exception as e:
        print(f"Error fetching Bitcoin block data: {e}")
        return None

def collect_attestations():
    """Prompt user for each attestation."""
    selected_attestations = []
    
    print("\nPlease confirm each attestation separately:")
    for i, attestation in enumerate(ATTESTATIONS, 1):
        while True:
            response = input(f"Confirm attestation {i}: '{attestation}' (y/n): ").lower()
            if response in ['y', 'n']:
                break
            print("Please answer 'y' or 'n'.")
        
        if response == 'y':
            selected_attestations.append(attestation)
    
    return selected_attestations

def create_warrant_canary_message(config):
    """Create the warrant canary message with attestations and verification elements."""
    current_date = get_current_date()
    nist_time = get_nist_time()
    democracy_now_headline = get_democracy_now_headline()
    bitcoin_block = get_bitcoin_latest_block()
    
    # Check if all required elements are available
    if not all([nist_time, democracy_now_headline, bitcoin_block]):
        missing = []
        if not nist_time: missing.append("NIST time")
        if not democracy_now_headline: missing.append("Democracy Now! headline")
        if not bitcoin_block: missing.append("Bitcoin block data")
        print(f"Error: Could not fetch: {', '.join(missing)}")
        return None
    
    # Collect attestations from user
    attestations = collect_attestations()
    if not attestations:
        print("Warning: No attestations were confirmed.")
        proceed = input("Do you want to proceed without any attestations? (y/n): ").lower()
        if proceed != 'y':
            print("Operation cancelled")
            return None
    
    # Create the message
    message = f"""We2.ee Warrant Canary
Date: {current_date}

"""
    
    # Add attestations
    for i, attestation in enumerate(attestations, 1):
        message += f"{i}. {attestation}\n"
    
    message += f"""
Proofs:
NIST time: {nist_time}
Democracy Now! headline: "{democracy_now_headline}"
Bitcoin block #{bitcoin_block['height']} hash: {bitcoin_block['hash']}
Bitcoin block time: {bitcoin_block['time']}

"""
    return message

def sign_with_gpg(message, gpg_key_id):
    """Sign the warrant canary message with GPG."""
    try:
        # Write message to temporary file
        with open(TEMP_MESSAGE_FILE, "w") as f:
            f.write(message)
        
        # Sign the message with GPG
        cmd = ["gpg", "--clearsign", "--default-key", gpg_key_id, TEMP_MESSAGE_FILE]
        subprocess.run(cmd, check=True)
        
        # Read the signed message
        with open(f"{TEMP_MESSAGE_FILE}.asc", "r") as f:
            signed_message = f.read()
            
        # Clean up temporary files
        os.remove(TEMP_MESSAGE_FILE)
        os.remove(f"{TEMP_MESSAGE_FILE}.asc")
        
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
    """Post the signed warrant canary to Matrix room using nio library."""
    if not config.get('matrix', {}).get('enabled', False):
        print("Matrix posting is disabled in config")
        return False
    
    try:
        from nio import AsyncClient, LoginResponse
        
        # Get Matrix config
        homeserver = config['matrix']['homeserver']
        username = config['matrix']['username']
        password = config['matrix']['password']
        room_id = config['matrix']['room_id']
        
        # Extract username without domain for login
        user_id = username
        if username.startswith('@'):
            user_id = username[1:]  # Remove @ prefix
        if ':' in user_id:
            user_id = user_id.split(':')[0]  # Remove server part
        
        # Create client
        client = AsyncClient(homeserver, username)
        
        # Login
        print(f"Logging in as {username} on {homeserver}...")
        response = await client.login(password)
        
        if isinstance(response, LoginResponse):
            print("Login successful")
        else:
            print(f"Matrix login failed: {response}")
            await client.close()
            return False
        
        # Format message for Matrix
        print(f"Posting canary to room {room_id}...")
        try:
            # Use HTML formatting for the message
            content = {
                "msgtype": "m.text",
                "body": signed_message,  # Plain text version
                "format": "org.matrix.custom.html",
                "formatted_body": f"<pre>{signed_message}</pre>"  # HTML version with preformatted text
            }
            
            response = await client.room_send(
                room_id=room_id,
                message_type="m.room.message",
                content=content
            )
            
            print("Successfully posted warrant canary to Matrix room")
        except Exception as e:
            print(f"Error sending message: {e}")
            await client.close()
            return False
        
        # Logout and close
        await client.logout()
        await client.close()
        
        return True
    except ImportError:
        print("Error: matrix-nio library not installed. Install with: pip install matrix-nio")
        return False
    except Exception as e:
        print(f"Error posting to Matrix: {e}")
        return False

def main():
    print("Generating We2.ee warrant canary...")
    
    # Load configuration
    config = load_config()
    gpg_key_id = config['gpg']['key_id']
    
    # Create message
    message = create_warrant_canary_message(config)
    if not message:
        print("Failed to create warrant canary message")
        sys.exit(1)
    
    # Display the message
    print("\nWarrant Canary Message Preview:")
    print("-" * 50)
    print(message)
    print("-" * 50)
    
    # Confirm with user
    user_input = input("\nDo you want to sign this message with GPG? (y/n): ")
    if user_input.lower() != 'y':
        print("Operation cancelled")
        sys.exit(0)
    
    # Sign and save
    signed_message = sign_with_gpg(message, gpg_key_id)
    if not signed_message:
        print("Failed to sign warrant canary message")
        sys.exit(1)
    
    if save_warrant_canary(signed_message):
        print("Warrant canary generated successfully!")
    else:
        print("Failed to save warrant canary")
        sys.exit(1)
    
    # Post to Matrix if enabled
    if config.get('matrix', {}).get('enabled', False):
        post_to_matrix_input = input("\nDo you want to post the warrant canary to Matrix? (y/n): ")
        if post_to_matrix_input.lower() == 'y':
            asyncio.run(post_to_matrix(config, signed_message))

if __name__ == "__main__":
    main()
