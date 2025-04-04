import os
from datetime import datetime as datetime
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from sw1tch import BASE_DIR, config, logger, read_registration_token
from sw1tch.utilities.time import get_current_utc, is_registration_closed
from sw1tch.utilities.registration import check_email_cooldown, check_username_availability, build_email_message, send_email_message
from sw1tch import save_registration, is_ip_banned, is_email_banned

router = APIRouter()
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

@router.get("/", response_class=HTMLResponse)
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

@router.get("/api/time")
async def get_server_time():
    now = get_current_utc()
    return JSONResponse({"utc_time": now.strftime("%H:%M:%S")})

@router.post("/register", response_class=HTMLResponse)
async def register(request: Request, requested_username: str = Form(...), email: str = Form(...)):
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
