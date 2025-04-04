import os
import yaml
import json
import logging
import re
import hashlib
from typing import List, Dict, Pattern
from fastapi import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from ipaddress import IPv4Address, IPv4Network

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
DATA_DIR = os.path.join(BASE_DIR, "data")
LOGS_DIR = os.path.join(BASE_DIR, "logs")

CONFIG_PATH = os.path.join(CONFIG_DIR, "config.yaml")
with open(CONFIG_PATH, "r") as f:
    config = yaml.safe_load(f)

REGISTRATIONS_PATH = os.path.join(DATA_DIR, "registrations.json")

def load_registrations() -> List[Dict]:
    try:
        with open(REGISTRATIONS_PATH, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def save_registrations(registrations: List[Dict]):
    with open(REGISTRATIONS_PATH, "w") as f:
        json.dump(registrations, f, indent=2)

def save_registration(data: Dict):
    registrations = load_registrations()
    registrations.append(data)
    save_registrations(registrations)

def load_banned_usernames() -> List[Pattern]:
    patterns = []
    try:
        with open(os.path.join(CONFIG_DIR, "banned_usernames.txt"), "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        patterns.append(re.compile(line, re.IGNORECASE))
                    except re.error:
                        logging.error(f"Invalid regex pattern in banned_usernames.txt: {line}")
    except FileNotFoundError:
        pass
    return patterns

def is_ip_banned(ip: str) -> bool:
    try:
        check_ip = IPv4Address(ip)
        try:
            with open(os.path.join(CONFIG_DIR, "banned_ips.txt"), "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        if '/' in line:
                            if check_ip in IPv4Network(line):
                                return True
                        else:
                            if check_ip == IPv4Address(line):
                                return True
                    except ValueError:
                        logging.error(f"Invalid IP/CIDR in banned_ips.txt: {line}")
        except FileNotFoundError:
            return False
    except ValueError:
        logging.error(f"Invalid IP address to check: {ip}")
    return False

def is_email_banned(email: str) -> bool:
    try:
        with open(os.path.join(CONFIG_DIR, "banned_emails.txt"), "r") as f:
            for line in f:
                pattern = line.strip()
                if not pattern:
                    continue
                regex_pattern = pattern.replace(".", "\\.").replace("*", ".*")
                try:
                    if re.match(regex_pattern, email, re.IGNORECASE):
                        return True
                except re.error:
                    logging.error(f"Invalid email pattern in banned_emails.txt: {pattern}")
    except FileNotFoundError:
        pass
    return False

def is_username_banned(username: str) -> bool:
    patterns = load_banned_usernames()
    return any(pattern.search(username) for pattern in patterns)

def read_registration_token():
    token_path = os.path.join(DATA_DIR, ".registration_token")
    try:
        with open(token_path, "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        return None

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    filename=os.path.join(LOGS_DIR, "registration.log"),
    filemode='a'
)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

class CustomLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.url.path == "/api/time" or request.url.path.endswith('favicon.ico'):
            return await call_next(request)
        response = await call_next(request)
        logger.info(f"Request: {request.method} {request.url.path} - Status: {response.status_code}")
        return response

def verify_admin_auth(auth_token: str) -> None:
    expected_password = config["matrix_admin"].get("password", "")
    expected_hash = hashlib.sha256(expected_password.encode()).hexdigest()
    if auth_token != expected_hash:
        raise HTTPException(status_code=403, detail="Invalid authentication token")
