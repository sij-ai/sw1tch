#!/usr/bin/env python3
import os
import re
import yaml
import json
import smtplib
import httpx
import logging
import ipaddress
from datetime import datetime, timedelta
from email.message import EmailMessage
from typing import List, Dict, Optional, Tuple, Set, Pattern, Union
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from ipaddress import IPv4Network, IPv4Address

# ---------------------------------------------------------
# 1. Load configuration and setup paths
# ---------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")
with open(CONFIG_PATH, "r") as f:
    config = yaml.safe_load(f)

# Initialize or load registrations.json
REGISTRATIONS_PATH = os.path.join(BASE_DIR, "registrations.json")
def load_registrations() -> List[Dict]:
    try:
        with open(REGISTRATIONS_PATH, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

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
        with open(os.path.join(BASE_DIR, "banned_usernames.txt"), "r") as f:
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
            with open(os.path.join(BASE_DIR, "banned_ips.txt"), "r") as f:
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
        with open(os.path.join(BASE_DIR, "banned_emails.txt"), "r") as f:
            for line in f:
                pattern = line.strip()
                if not pattern:
                    continue
                # Convert email patterns to regex
                # Replace * with .* and escape dots
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

# Read the registration token
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
    format='%(asctime)s - %(levelname)s - %(message)s'
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
    reset_h = config["token_reset_time_utc"] // 100
    reset_m = config["token_reset_time_utc"] % 100
    candidate = now.replace(hour=reset_h, minute=reset_m, second=0, microsecond=0)
    if candidate <= now:
        # If we've passed today's reset time, it must be tomorrow.
        candidate += timedelta(days=1)
    return candidate

def get_downtime_start(next_reset: datetime) -> datetime:
    """Return the downtime start time (minutes before next_reset)."""
    return next_reset - timedelta(minutes=config["downtime_before_token_reset"])

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

    if not parts:  # If total is less than a minute
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
        # We are within downtime
        time_until_open = nr - now
        msg = (
            f"Registration is closed. "
            f"It reopens in {format_timedelta(time_until_open)} at {nr.strftime('%H:%M UTC')}."
        )
        return True, msg
    else:
        # Registration is open
        if now > ds:
            # We've passed ds, so next downtime is tomorrow
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
        
    if not config.get("multiple_users_per_email", True):
        return "This email address has already been used to register an account."
        
    email_cooldown = config.get("email_cooldown")
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
        
    url = f"https://{config['homeserver']}/_matrix/client/v3/register/available?username={username}"
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
def build_email_message(token: str, requested_username: str, now: datetime, recipient_email: str) -> EmailMessage:
    """
    Build and return an EmailMessage for registration.
    """
    time_until_reset = get_time_until_reset_str(now)
    
    # Format bodies using config templates
    plain_body = config["email_body"].format(
        homeserver=config["homeserver"],
        registration_token=token,
        requested_username=requested_username,
        utc_time=now.strftime("%H:%M:%S"),
        time_until_reset=time_until_reset
    )
    html_body = config.get("email_body_html", "").format(
        homeserver=config["homeserver"],
        registration_token=token,
        requested_username=requested_username,
        utc_time=now.strftime("%H:%M:%S"),
        time_until_reset=time_until_reset
    )
    
    msg = EmailMessage()
    msg.set_content(plain_body)
    
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    
    msg["Subject"] = config["email_subject"].format(homeserver=config["homeserver"])
    
    # Get the sender value from configuration.
    # Ensure it's fully-qualified: it must contain an "@".
    from_value = config["smtp"].get("from")
    if not from_value or "@" not in from_value:
        logger.warning(f"Sender address '{from_value}' is not fully-qualified. Falling back to {config['smtp']['username']}.")
        from_value = config["smtp"]["username"]
    
    msg["From"] = from_value
    
    msg["To"] = recipient_email
    return msg

def send_email_message(msg: EmailMessage) -> None:
    """
    Send an email message using SMTP configuration.
    """
    smtp_conf = config["smtp"]
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
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

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
            "reset_hour": config["token_reset_time_utc"] // 100,
            "reset_minute": config["token_reset_time_utc"] % 100,
            "downtime_minutes": config["downtime_before_token_reset"]
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
    
    # Build and send registration email
    email_message = build_email_message(token, requested_username, now, email)
    send_email_message(email_message)
    
    # Save registration data and log success
    registration_data = {
        "requested_name": requested_username,
        "email": email,
        "datetime": datetime.utcnow().isoformat(),
        "ip_address": client_ip
    }
    save_registration(registration_data)
    logger.info(f"Registration successful - Username: {requested_username}, Email: {email}")
    
    return templates.TemplateResponse("success.html", {"request": request, "homeserver": config["homeserver"]})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "registration:app", 
        host="0.0.0.0", 
        port=config["port"], 
        reload=True,
        access_log=False
    )
