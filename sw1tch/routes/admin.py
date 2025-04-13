from fastapi import APIRouter, Form, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from datetime import datetime, timedelta
import httpx
import re
import os
import hashlib

from sw1tch import BASE_DIR, config, logger, load_registrations, save_registrations, verify_admin_auth
from sw1tch.utilities.matrix import get_matrix_users, deactivate_user, get_matrix_rooms, get_room_members, check_banned_room_name

router = APIRouter(prefix="/_admin")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

@router.get("/", response_class=HTMLResponse)
async def admin_panel(request: Request, auth_token: str = Depends(verify_admin_auth)):
    return templates.TemplateResponse("admin.html", {"request": request, "authenticated": True})

@router.get("/login", response_class=HTMLResponse)
async def admin_login_page(request: Request):
    return templates.TemplateResponse("admin.html", {"request": request, "authenticated": False})

@router.post("/login", response_class=HTMLResponse)
async def admin_login(request: Request, password: str = Form(...)):
    expected_password = config["matrix_admin"].get("password", "")
    hashed_password = hashlib.sha256(password.encode()).hexdigest()
    if hashed_password == hashlib.sha256(expected_password.encode()).hexdigest():
        return HTMLResponse(
            content=f"""
            <html>
                <head>
                    <meta http-equiv="refresh" content="0;url=/_admin/?auth_token={hashed_password}">
                </head>
                <body>
                    <p>Redirecting to admin panel...</p>
                </body>
            </html>
            """
        )
    else:
        return templates.TemplateResponse("admin.html", {"request": request, "authenticated": False, "error": "Invalid password"})

@router.get("/view_unfulfilled", response_class=HTMLResponse)
async def view_unfulfilled_registrations(request: Request, auth_token: str = Depends(verify_admin_auth)):
    registrations = load_registrations()
    unfulfilled = []
    if registrations:
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
                        unfulfilled.append({
                            "username": username,
                            "email": entry["email"],
                            "registration_date": entry["datetime"],
                            "age_hours": age.total_seconds() / 3600
                        })
                except httpx.RequestError as ex:
                    logger.error(f"Error checking username {username}: {ex}")
    return templates.TemplateResponse("unfulfilled_registrations.html", {"request": request, "registrations": unfulfilled})

@router.post("/purge_unfulfilled_registrations", response_class=JSONResponse)
async def purge_unfulfilled_registrations(min_age_hours: int = Form(default=24), auth_token: str = Depends(verify_admin_auth)):
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

@router.get("/view_undocumented", response_class=HTMLResponse)
async def view_undocumented_users(request: Request, auth_token: str = Depends(verify_admin_auth)):
    registrations = load_registrations()
    matrix_users = await get_matrix_users()
    registered_usernames = {entry["requested_name"].lower() for entry in registrations}
    homeserver = config["homeserver"].lower()
    undocumented_users = [
        user for user in matrix_users
        if user.lower().startswith("@") and user[1:].lower().split(":", 1)[1] == homeserver
        and user[1:].lower().split(":", 1)[0] not in registered_usernames
    ]
    return templates.TemplateResponse("undocumented_users.html", {"request": request, "users": undocumented_users})

@router.post("/deactivate_undocumented_users", response_class=JSONResponse)
async def deactivate_undocumented_users(auth_token: str = Depends(verify_admin_auth)):
    registrations = load_registrations()
    matrix_users = await get_matrix_users()
    registered_usernames = {entry["requested_name"].lower() for entry in registrations}
    homeserver = config["homeserver"].lower()
    undocumented_users = [
        user for user in matrix_users
        if user.lower().startswith("@") and user[1:].lower().split(":", 1)[1] == homeserver
        and user[1:].lower().split(":", 1)[0] not in registered_usernames
    ]
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
    result = {"message": f"Deactivated {deactivated_count} undocumented user(s)", "deactivated_count": deactivated_count}
    if failed_deactivations:
        result["failed_deactivations"] = failed_deactivations
    return JSONResponse(result)

@router.post("/retroactively_document_users", response_class=JSONResponse)
async def retroactively_document_users(auth_token: str = Depends(verify_admin_auth)):
    registrations = load_registrations()
    matrix_users = await get_matrix_users()
    registered_usernames = {entry["requested_name"].lower() for entry in registrations}
    homeserver = config["homeserver"].lower()
    added_count = 0
    for user in matrix_users:
        if not user.lower().startswith("@"):
            continue
        username, user_homeserver = user[1:].lower().split(":", 1)
        if user_homeserver != homeserver or username in registered_usernames:
            continue
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

@router.get("/moderate_rooms", response_class=JSONResponse)
async def moderate_rooms(auth_token: str = Depends(verify_admin_auth)):
    all_rooms = []
    banned_rooms = []
    page = 1
    while True:
        rooms = await get_matrix_rooms(page)
        if not rooms:
            break
        # Stop if all rooms on this page have fewer than 3 members
        if all(room["members"] < 3 for room in rooms):
            break
        for room in rooms:
            # Add every room to all_rooms, even those with < 3 members, for debugging
            all_rooms.append({
                "room_id": room["room_id"],
                "room_name": room["name"],
                "total_members": room["members"]
            })
            if room["members"] < 3:
                continue  # Skip rooms with fewer than 3 members for banned_rooms
            if check_banned_room_name(room["name"]):
                # Get local members for banned rooms
                members_info = await get_room_members(room["room_id"], local_only=True)
                banned_rooms.append({
                    "room_id": room["room_id"],
                    "room_name": room["name"],
                    "total_members": room["members"],
                    "local_users": [
                        {"user_id": member["user_id"], "display_name": member["display_name"]}
                        for member in members_info["local_members"]
                    ]
                })
        page += 1
    return JSONResponse({"all_rooms": all_rooms, "banned_rooms": banned_rooms})
