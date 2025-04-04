import os
import smtplib
import httpx
from datetime import datetime
from email.message import EmailMessage
from typing import Optional
from fastapi import HTTPException

from sw1tch import config, BASE_DIR, load_registrations, save_registration, is_username_banned, logger

async def check_username_availability(username: str) -> bool:
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
                is_available = response.json().get("available", False)
                logger.info(f"[USERNAME CHECK] {username}: {'Available' if is_available else 'Taken'}")
                return is_available
            elif response.status_code == 400:
                logger.info(f"[USERNAME CHECK] {username}: Taken (400)")
                return False
        except httpx.RequestError as ex:
            logger.warning(f"[USERNAME CHECK] Could not reach homeserver: {ex}")
            return False
    return False

def check_email_cooldown(email: str) -> Optional[str]:
    registrations = load_registrations()
    email_entries = [r for r in registrations if r["email"] == email]
    if not email_entries:
        return None
    if not config["registration"].get("multiple_users_per_email", True):
        return "This email address has already been used to register an account."
    email_cooldown = config["registration"].get("email_cooldown")
    if email_cooldown:
        latest = max(datetime.fromisoformat(e["datetime"]) for e in email_entries)
        time_since = datetime.utcnow() - latest
        if time_since.total_seconds() < email_cooldown:
            wait_time = email_cooldown - time_since.total_seconds()
            return f"Please wait {int(wait_time)} seconds before requesting another account."
    return None

def load_template(template_path: str) -> str:
    try:
        with open(os.path.join(BASE_DIR, template_path), "r") as f:
            return f.read()
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail=f"Email template not found: {template_path}")

def build_email_message(token: str, requested_username: str, now: datetime, recipient_email: str) -> EmailMessage:
    from sw1tch.utilities.time import get_time_until_reset_str
    time_until_reset = get_time_until_reset_str(now)
    plain_template = load_template(config["email"]["templates"]["registration_token"]["body"])
    html_template = load_template(config["email"]["templates"]["registration_token"]["body_html"])
    plain_body = plain_template.format(
        homeserver=config["homeserver"], registration_token=token,
        requested_username=requested_username, utc_time=now.strftime("%H:%M:%S"),
        time_until_reset=time_until_reset
    )
    html_body = html_template.format(
        homeserver=config["homeserver"], registration_token=token,
        requested_username=requested_username, utc_time=now.strftime("%H:%M:%S"),
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
