#!/usr/bin/env python3
import os
import re
import yaml
import json
import smtplib
import httpx
import logging
import ipaddress
import hashlib
import asyncio
import time
from datetime import datetime, timedelta
from email.message import EmailMessage
from typing import List, Dict, Optional, Tuple, Set, Pattern, Union
from fastapi import FastAPI, Request, Form, HTTPException, Depends, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from ipaddress import IPv4Network, IPv4Address
from nio import AsyncClient, RoomMessageText, RoomMessageNotice

# ---------------------------------------------------------
# 1. Load configuration and setup paths
# ---------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
DATA_DIR = os.path.join(BASE_DIR, "data")
LOGS_DIR = os.path.join(BASE_DIR, "logs")

CONFIG_PATH = os.path.join(CONFIG_DIR, "config.yaml")
with open(CONFIG_PATH, "r") as f:
    config = yaml.safe_load(f)

# Initialize or load registrations.json
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
    with open(REGISTRATIONS_PATH, "w") as f:
        json.dump(registrations, f, indent=2)

# Functions to check banned entries
def load_banned_usernames() -> List[Pattern]:
    """Load banned usernames file and compile regex patterns."""
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
    """Check if an IP is banned, supporting both individual IPs and CIDR ranges."""
    try:
        check_ip = IPv4Address(ip)
        try:
            with open(os.path.join(CONFIG_DIR, "banned_ips.txt"), "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        if '/' in line:  # CIDR notation
                            if check_ip in IPv4Network(line):
                                return True
                        else:  # Individual IP
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
    """Check if an email matches any banned patterns."""
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
    """Check if username matches any banned patterns."""
    patterns = load_banned_usernames()
    return any(pattern.search(username) for pattern in patterns)

# Read the registration token (still at base level as per shell script)
def read_registration_token():
    token_path = os.path.join(BASE_DIR, ".registration_token")
    try:
        with open(token_path, "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        return None

# ---------------------------------------------------------
# 2. Logging Configuration
# ---------------------------------------------------------
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

# ---------------------------------------------------------
# 3. Time Calculation Functions
# ---------------------------------------------------------
def get_current_utc() -> datetime:
    return datetime.utcnow()

def get_next_reset_time(now: datetime) -> datetime:
    """Return the next reset time (possibly today or tomorrow) from config."""
    reset_h = config["registration"]["token_reset_time_utc"] // 100
    reset_m = config["registration"]["token_reset_time_utc"] % 100
    candidate = now.replace(hour=reset_h, minute=reset_m, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate

def get_downtime_start(next_reset: datetime) -> datetime:
    """Return the downtime start time (minutes before next_reset)."""
    return next_reset - timedelta(minutes=config["registration"]["downtime_before_token_reset"])

def format_timedelta(td: timedelta) -> str:
    """Format a timedelta as 'X hours and Y minutes' (or similar)."""
    total_minutes = int(td.total_seconds() // 60)
    hours = total_minutes // 60
    minutes = total_minutes % 60

    parts = []
    if hours == 1:
        parts.append("1 hour")
    elif hours > 1:
        parts.append(f"{hours} hours")

    if minutes == 1:
        parts.append("1 minute")
    elif minutes > 1:
        parts.append(f"{minutes} minutes")

    if not parts:
        return "0 minutes"

    return " and ".join(parts)

def get_time_until_reset_str(now: datetime) -> str:
    """Return a string like '3 hours and 41 minutes' until next reset."""
    nr = get_next_reset_time(now)
    delta = nr - now
    return format_timedelta(delta)

def is_registration_closed(now: datetime) -> Tuple[bool, str]:
    """
    Determine if registration is closed based on config.
    Return (closed_bool, message).
    """
    nr = get_next_reset_time(now)
    ds = get_downtime_start(nr)

    if ds <= now < nr:
        time_until_open = nr - now
        msg = (
            f"Registration is closed. "
            f"It reopens in {format_timedelta(time_until_open)} at {nr.strftime('%H:%M UTC')}."
        )
        return True, msg
    else:
        if now > ds:
            nr += timedelta(days=1)
            ds = get_downtime_start(nr)

        time_until_close = ds - now
        msg = (
            f"Registration is open. "
            f"It will close in {format_timedelta(time_until_close)} at {ds.strftime('%H:%M UTC')}."
        )
        return False, msg

# ---------------------------------------------------------
# 4. Registration Validation
# ---------------------------------------------------------
def check_email_cooldown(email: str) -> Optional[str]:
    """Check if email is allowed to register based on cooldown and multiple account rules."""
    registrations = load_registrations()
    email_entries = [r for r in registrations if r["email"] == email]
    
    if not email_entries:
        return None
        
    if not config["registration"].get("multiple_users_per_email", True):
        return "This email address has already been used to register an account."
        
    email_cooldown = config["registration"].get("email_cooldown")
    if email_cooldown:
        latest_registration = max(
            datetime.fromisoformat(e["datetime"]) 
            for e in email_entries
        )
        time_since = datetime.utcnow() - latest_registration
        if time_since.total_seconds() < email_cooldown:
            wait_time = email_cooldown - time_since.total_seconds()
            return f"Please wait {int(wait_time)} seconds before requesting another account."
            
    return None

async def check_username_availability(username: str) -> bool:
    """Check if username is available on Matrix and in our registration records."""
    if is_username_banned(username):
        logger.info(f"[USERNAME CHECK] {username}: Banned by pattern")
        return False

    registrations = load_registrations()
    if any(r["requested_name"] == username for r in registrations):
        logger.info(f"[USERNAME CHECK] {username}: Already requested")
        return False
        
    url = f"{config['base_url']}/_matrix/client/v3/register/available?username={username}"
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, timeout=5)
            if response.status_code == 200:
                data = response.json()
                is_available = data.get("available", False)
                logger.info(f"[USERNAME CHECK] {username}: {'Available' if is_available else 'Taken'}")
                return is_available
            elif response.status_code == 400:
                logger.info(f"[USERNAME CHECK] {username}: Taken (400)")
                return False
        except httpx.RequestError as ex:
            logger.warning(f"[USERNAME CHECK] Could not reach homeserver: {ex}")
            return False
    return False

# ---------------------------------------------------------
# 5. Email Helper Functions
# ---------------------------------------------------------
def load_template(template_path: str) -> str:
    """Load an email template from a file."""
    try:
        with open(os.path.join(BASE_DIR, template_path), "r") as f:
            return f.read()
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail=f"Email template not found: {template_path}")

def build_email_message(token: str, requested_username: str, now: datetime, recipient_email: str) -> EmailMessage:
    """
    Build and return an EmailMessage for registration using file-based templates.
    """
    time_until_reset = get_time_until_reset_str(now)
    
    plain_template = load_template(config["email"]["templates"]["registration_token"]["body"])
    html_template = load_template(config["email"]["templates"]["registration_token"]["body_html"])
    
    plain_body = plain_template.format(
        homeserver=config["homeserver"],
        registration_token=token,
        requested_username=requested_username,
        utc_time=now.strftime("%H:%M:%S"),
        time_until_reset=time_until_reset
    )
    html_body = html_template.format(
        homeserver=config["homeserver"],
        registration_token=token,
        requested_username=requested_username,
        utc_time=now.strftime("%H:%M:%S"),
        time_until_reset=time_until_reset
    )
    
    msg = EmailMessage()
    msg.set_content(plain_body)
    msg.add_alternative(html_body, subtype="html")
    
    msg["Subject"] = config["email"]["templates"]["registration_token"]["subject"].format(homeserver=config["homeserver"])
    msg["From"] = config["email"]["smtp"]["from"]
    msg["To"] = recipient_email
    return msg

def send_email_message(msg: EmailMessage) -> None:
    """
    Send an email message using SMTP configuration.
    """
    smtp_conf = config["email"]["smtp"]
    try:
        with smtplib.SMTP(smtp_conf["host"], smtp_conf["port"]) as server:
            if smtp_conf.get("use_tls", True):
                server.starttls()
            server.login(smtp_conf["username"], smtp_conf["password"])
            server.send_message(msg)
            logger.info(f"Registration email sent successfully to {msg['To']}")
    except Exception as ex:
        logger.error(f"Failed to send email: {ex}")
        raise HTTPException(status_code=500, detail=f"Error sending email: {ex}")

# ---------------------------------------------------------
# 6. FastAPI Setup and Routes
# ---------------------------------------------------------
app = FastAPI()
app.add_middleware(CustomLoggingMiddleware)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# Dependency for admin authentication
def verify_admin_auth(auth_token: str = Form(...)) -> None:
    """Verify the SHA256 hash of matrix_admin.password from config.yaml."""
    expected_password = config["matrix_admin"].get("password", "")
    expected_hash = hashlib.sha256(expected_password.encode()).hexdigest()
    if auth_token != expected_hash:
        raise HTTPException(status_code=403, detail="Invalid authentication token")

# Helper function to parse Matrix responses
def parse_response(response_text: str, query: str) -> Dict[str, Union[str, List[str]]]:
    """Parse a response that may contain a markdown codeblock."""
    query_parts = query.strip().split()
    array_key = query_parts[0] if query_parts else "data"
    codeblock_pattern = r"(.*?):\s*\n```\s*\n([\s\S]*?)\n```"
    match = re.search(codeblock_pattern, response_text)
    if match:
        message = match.group(1).strip()
        codeblock_content = match.group(2)
        items = [line for line in codeblock_content.split('\n') if line.strip()]
        return {"message": message, array_key: items}
    return {"response": response_text}

# Helper function to get the list of users from the Matrix admin room
async def get_matrix_users() -> List[str]:
    """Fetch the list of users from the Matrix admin room."""
    matrix_config = config["matrix_admin"]
    homeserver = config["base_url"]
    username = matrix_config.get("username")
    password = matrix_config.get("password")
    admin_room = matrix_config.get("room")
    admin_response_user = matrix_config.get("super_admin")

    if not all([homeserver, username, password, admin_room, admin_response_user]):
        raise HTTPException(status_code=500, detail="Incomplete Matrix admin configuration")

    client = AsyncClient(homeserver, username)
    try:
        login_response = await client.login(password)
        if getattr(login_response, "error", None):
            raise Exception(f"Login error: {login_response.error}")
        logger.debug("Successfully logged in to Matrix")

        await client.join(admin_room)
        initial_sync = await client.sync(timeout=5000)
        next_batch = initial_sync.next_batch

        await client.room_send(
            room_id=admin_room,
            message_type="m.room.message",
            content={"msgtype": "m.text", "body": "!admin users list-users"},
        )
        query_time = time.time()

        timeout_seconds = 10
        start_time = time.time()
        response_message = None

        while (time.time() - start_time) < timeout_seconds:
            sync_response = await client.sync(timeout=2000, since=next_batch)
            next_batch = sync_response.next_batch
            room = sync_response.rooms.join.get(admin_room)
            if room and room.timeline and room.timeline.events:
                message_events = [
                    event for event in room.timeline.events
                    if isinstance(event, (RoomMessageText, RoomMessageNotice))
                ]
                for event in message_events:
                    event_time = event.server_timestamp / 1000.0
                    if event.sender == admin_response_user and event_time >= query_time:
                        response_message = event.body
                        logger.debug(f"Found response: {response_message[:100]}...")
                        break
                if response_message:
                    break

        await client.logout()
        await client.close()

        if not response_message:
            raise HTTPException(status_code=504, detail="No response from admin user within timeout")

        parsed = parse_response(response_message, "users list-users")
        return parsed.get("users", [])
    except Exception as e:
        await client.close()
        logger.error(f"Error fetching Matrix users: {e}")
        raise HTTPException(status_code=500, detail=f"Error fetching users: {e}")

# Helper function to deactivate a user via Matrix admin room
async def deactivate_user(user: str) -> bool:
    """Send a deactivation command for a user to the Matrix admin room."""
    matrix_config = config["matrix_admin"]
    homeserver = config["base_url"]
    username = matrix_config.get("username")
    password = matrix_config.get("password")
    admin_room = matrix_config.get("room")
    admin_response_user = matrix_config.get("super_admin")

    client = AsyncClient(homeserver, username)
    try:
        login_response = await client.login(password)
        if getattr(login_response, "error", None):
            raise Exception(f"Login error: {login_response.error}")
        logger.debug(f"Logged in to deactivate {user}")

        await client.join(admin_room)
        await client.sync(timeout=5000)

        command = f"!admin users deactivate {user}"
        await client.room_send(
            room_id=admin_room,
            message_type="m.room.message",
            content={"msgtype": "m.text", "body": command},
        )
        logger.info(f"Sent deactivation command for {user}")

        await asyncio.sleep(1)
        await client.logout()
        await client.close()
        return True
    except Exception as e:
        await client.close()
        logger.error(f"Failed to deactivate {user}: {e}")
        return False

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    now = get_current_utc()
    closed, message = is_registration_closed(now)
    
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "registration_closed": closed,
            "homeserver": config["homeserver"],
            "message": message,
            "reset_hour": config["registration"]["token_reset_time_utc"] // 100,
            "reset_minute": config["registration"]["token_reset_time_utc"] % 100,
            "downtime_minutes": config["registration"]["downtime_before_token_reset"]
        }
    )

@app.get("/api/time")
async def get_server_time():
    now = get_current_utc()
    return JSONResponse({"utc_time": now.strftime("%H:%M:%S")})

@app.post("/register", response_class=HTMLResponse)
async def register(
    request: Request,
    requested_username: str = Form(...),
    email: str = Form(...)
):
    now = get_current_utc()
    client_ip = request.client.host

    logger.info(f"Registration attempt - Username: {requested_username}, Email: {email}, IP: {client_ip}")
    
    closed, message = is_registration_closed(now)
    if closed:
        logger.info("Registration rejected: Registration is closed")
        return templates.TemplateResponse("error.html", {"request": request, "message": message})
    
    if is_ip_banned(client_ip):
        logger.info(f"Registration rejected: Banned IP {client_ip}")
        return templates.TemplateResponse("error.html", {"request": request, "message": "Registration not allowed from your IP address."})
    
    if is_email_banned(email):
        logger.info(f"Registration rejected: Banned email {email}")
        return templates.TemplateResponse("error.html", {"request": request, "message": "Registration not allowed for this email address."})
    
    if error_message := check_email_cooldown(email):
        logger.info(f"Registration rejected: Email cooldown - {email}")
        return templates.TemplateResponse("error.html", {"request": request, "message": error_message})
    
    available = await check_username_availability(requested_username)
    if not available:
        logger.info(f"Registration rejected: Username unavailable - {requested_username}")
        return templates.TemplateResponse("error.html", {"request": request, "message": f"The username '{requested_username}' is not available."})
    
    token = read_registration_token()
    if token is None:
        logger.error("Registration token file not found")
        raise HTTPException(status_code=500, detail="Registration token file not found.")
    
    email_message = build_email_message(token, requested_username, now, email)
    send_email_message(email_message)
    
    registration_data = {
        "requested_name": requested_username,
        "email": email,
        "datetime": datetime.utcnow().isoformat(),
        "ip_address": client_ip
    }
    save_registration(registration_data)
    logger.info(f"Registration successful - Username: {requested_username}, Email: {email}")
    
    return templates.TemplateResponse("success.html", {"request": request, "homeserver": config["homeserver"]})

@app.post("/_admin/purge_unfulfilled_registrations", response_class=JSONResponse)
async def purge_unfulfilled_registrations(
    min_age_hours: int = Form(default=24),
    auth_token: str = Depends(verify_admin_auth)
):
    """
    Purge unfulfilled registration entries older than min_age_hours where the username
    does not exist on the homeserver.
    """
    registrations = load_registrations()
    if not registrations:
        return JSONResponse({"message": "No registrations found to clean up"})

    logger.info(f"Starting cleanup of {len(registrations)} registrations")
    logger.info(f"Will remove non-existent users registered more than {min_age_hours} hours ago")

    entries_to_keep = []
    removed_count = 0
    too_new_count = 0
    exists_count = 0
    current_time = datetime.utcnow()

    async with httpx.AsyncClient() as client:
        for entry in registrations:
            username = entry["requested_name"]
            reg_date = datetime.fromisoformat(entry["datetime"])
            age = current_time - reg_date

            url = f"{config['base_url']}/_matrix/client/v3/register/available?username={username}"
            try:
                response = await client.get(url, timeout=5)
                if response.status_code == 200 and response.json().get("available", False):
                    exists = False
                elif response.status_code == 400 or (response.status_code == 200 and not response.json().get("available", False)):
                    exists = True
                else:
                    logger.warning(f"Unexpected response for {username}: {response.status_code}")
                    exists = False
            except httpx.RequestError as ex:
                logger.error(f"Error checking username {username}: {ex}")
                exists = False

            if exists:
                entries_to_keep.append(entry)
                exists_count += 1
                logger.info(f"Keeping registration for existing user: {username}")
                continue

            if age < timedelta(hours=min_age_hours):
                entries_to_keep.append(entry)
                too_new_count += 1
                logger.info(f"Keeping recent registration: {username} (age: {age.total_seconds()/3600:.1f} hours)")
            else:
                logger.info(f"Removing old registration: {username} (age: {age.total_seconds()/3600:.1f} hours)")
                removed_count += 1

    save_registrations(entries_to_keep)

    result = {
        "message": "Cleanup complete",
        "kept_existing": exists_count,
        "kept_recent": too_new_count,
        "removed": removed_count,
        "total_remaining": len(entries_to_keep)
    }
    logger.info(f"Cleanup complete: {result}")
    return JSONResponse(result)

@app.post("/_admin/deactivate_undocumented_users", response_class=JSONResponse)
async def deactivate_undocumented_users(auth_token: str = Depends(verify_admin_auth)):
    """Deactivate users on the homeserver without matching entries in registrations.json."""
    registrations = load_registrations()
    matrix_users = await get_matrix_users()

    registered_usernames = {entry["requested_name"].lower() for entry in registrations}
    homeserver = config["homeserver"].lower()

    undocumented_users = []
    for user in matrix_users:
        if not user.lower().startswith("@"):
            continue
        username, user_homeserver = user[1:].lower().split(":", 1)
        if user_homeserver != homeserver:
            continue
        if username not in registered_usernames:
            undocumented_users.append(user)

    if not undocumented_users:
        logger.info("No undocumented users found to deactivate")
        return JSONResponse({"message": "No undocumented users found to deactivate", "deactivated_count": 0})

    deactivated_count = 0
    failed_deactivations = []

    for user in undocumented_users:
        success = await deactivate_user(user)
        if success:
            deactivated_count += 1
        else:
            failed_deactivations.append(user)

    logger.info(f"Deactivated {deactivated_count} undocumented users")
    if failed_deactivations:
        logger.warning(f"Failed to deactivate {len(failed_deactivations)} users: {failed_deactivations}")

    result = {
        "message": f"Deactivated {deactivated_count} undocumented user(s)",
        "deactivated_count": deactivated_count
    }
    if failed_deactivations:
        result["failed_deactivations"] = failed_deactivations
    return JSONResponse(result)

@app.post("/_admin/retroactively_document_users", response_class=JSONResponse)
async def retroactively_document_users(auth_token: str = Depends(verify_admin_auth)):
    """Add entries to registrations.json for undocumented users."""
    registrations = load_registrations()
    matrix_users = await get_matrix_users()

    registered_usernames = {entry["requested_name"].lower() for entry in registrations}
    homeserver = config["homeserver"].lower()
    added_count = 0

    for user in matrix_users:
        if not user.lower().startswith("@"):
            continue
        username, user_homeserver = user[1:].lower().split(":", 1)
        if user_homeserver != homeserver:
            continue
        if username not in registered_usernames:
            new_entry = {
                "requested_name": username,
                "email": "null@nope.no",
                "datetime": datetime.utcnow().isoformat(),
                "ip_address": "127.0.0.1"
            }
            registrations.append(new_entry)
            registered_usernames.add(username)
            added_count += 1
            logger.info(f"Added retroactive entry for {user}")

    if added_count > 0:
        save_registrations(registrations)
        logger.info(f"Retroactively documented {added_count} users")

    return JSONResponse({
        "message": f"Retroactively documented {added_count} user(s)",
        "added_count": added_count
    })

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "registration:app", 
        host="0.0.0.0", 
        port=config["port"], 
        reload=True,
        access_log=False
    )
