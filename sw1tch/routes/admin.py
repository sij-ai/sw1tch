from fastapi import APIRouter, Form, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from datetime import datetime, timedelta
import httpx
import re
import os
import hashlib
import json
import asyncio

from sw1tch import BASE_DIR, config, logger, load_registrations, save_registrations, verify_admin_auth
from sw1tch.utilities.matrix import (
    get_matrix_users, 
    deactivate_user, 
    get_matrix_rooms, 
    get_room_members, 
    check_banned_room_name,
    get_matched_pattern,
    matrix_bot
)

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

# Old endpoint - kept for backwards compatibility but will timeout
@router.get("/moderate_rooms", response_class=JSONResponse)
async def moderate_rooms(auth_token: str = Depends(verify_admin_auth)):
    all_rooms = []
    banned_rooms = []
    page = 1
    while True:
        rooms = await get_matrix_rooms(page)
        if not rooms:
            break
        if all(room["members"] < 3 for room in rooms):
            break
        for room in rooms:
            all_rooms.append({
                "room_id": room["room_id"],
                "room_name": room["name"],
                "total_members": room["members"]
            })
            if room["members"] < 3:
                continue
            if check_banned_room_name(room["name"]):
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

# Helper functions for streaming endpoint
def parse_rooms_response(response: str) -> list:
    """Parse rooms from admin bot response."""
    room_pattern = r"(!\S+)\s+Members: (\d+)\s+Name: (.*)"
    rooms = []
    for line in response.split('\n'):
        match = re.match(room_pattern, line)
        if match:
            rooms.append({
                'room_id': match.group(1),
                'members': int(match.group(2)),
                'name': match.group(3).strip()
            })
    return rooms

def parse_members_response(response: str) -> list:
    """Parse members from admin bot response."""
    member_pattern = r"(@\S+)\s*\|\s*(\S+)"
    members = []
    for line in response.split('\n'):
        match = re.match(member_pattern, line)
        if match:
            members.append({
                'user_id': match.group(1),
                'display_name': match.group(2)
            })
    return members

# New streaming endpoint
@router.get("/moderate_rooms_stream")
async def moderate_rooms_stream(auth_token: str = Depends(verify_admin_auth)):
    """Stream room moderation results as they're discovered."""
    
    async def event_generator():
        try:
            yield f"data: {json.dumps({'status': 'starting', 'message': 'Connecting to Matrix...'})}\n\n"
            
            await matrix_bot.ensure_connected()
            yield f"data: {json.dumps({'status': 'connected', 'message': 'Fetching rooms...'})}\n\n"
            
            page = 1
            total_rooms = 0
            total_banned = 0
            
            while True:
                yield f"data: {json.dumps({'status': 'progress', 'page': page, 'message': f'Checking page {page}...'})}\n\n"
                
                try:
                    command = f"!admin rooms list-rooms {page} --exclude-banned --exclude-disabled"
                    response = await matrix_bot.send_admin_command(command, timeout=20)
                    rooms = parse_rooms_response(response)
                    
                    if not rooms:
                        break
                    
                    if all(room["members"] < 3 for room in rooms):
                        yield f"data: {json.dumps({'status': 'info', 'message': 'Reached DMs/small rooms, stopping'})}\n\n"
                        break
                    
                    total_rooms += len(rooms)
                    
                    for room in rooms:
                        if room["members"] < 3:
                            continue
                        
                        if check_banned_room_name(room["name"]):
                            total_banned += 1
                            matched_pattern = get_matched_pattern(room["name"])
                            
                            yield f"data: {json.dumps({
                                'type': 'banned_room_found',
                                'room': {
                                    'room_id': room['room_id'],
                                    'room_name': room['name'],
                                    'total_members': room['members'],
                                    'matched_pattern': matched_pattern,
                                    'timestamp': datetime.utcnow().isoformat()
                                },
                                'total_found': total_banned
                            })}\n\n"
                            
                            try:
                                command = f"!admin rooms info list-joined-members {room['room_id']} --local-only"
                                members_response = await matrix_bot.send_admin_command(command, timeout=30)
                                members = parse_members_response(members_response)
                                
                                yield f"data: {json.dumps({
                                    'type': 'room_members',
                                    'room_id': room['room_id'],
                                    'local_users': members
                                })}\n\n"
                            except TimeoutError as e:
                                logger.error(f"Timeout fetching members for {room['room_id']}: {e}")
                                yield f"data: {json.dumps({
                                    'type': 'error',
                                    'message': f"Timeout fetching members for {room['name']}"
                                })}\n\n"
                            except Exception as e:
                                logger.error(f"Error fetching members: {e}")
                                yield f"data: {json.dumps({
                                    'type': 'error',
                                    'message': f"Could not fetch members for {room['name']}: {str(e)}"
                                })}\n\n"
                    
                    page += 1
                    
                except Exception as e:
                    logger.error(f"Error on page {page}: {e}")
                    yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
                    break
            
            yield f"data: {json.dumps({
                'status': 'complete',
                'message': f'Scan complete. Checked {total_rooms} rooms, found {total_banned} with banned names.'
            })}\n\n"
            
        except Exception as e:
            logger.error(f"Fatal error in moderation stream: {e}")
            yield f"data: {json.dumps({'status': 'error', 'message': str(e)})}\n\n"
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )

# Admin command helper - uses persistent bot with proper response checking
async def send_matrix_admin_command(command: str, expected_pattern: str = None) -> dict:
    """Send admin command and return result."""
    try:
        logger.info(f"Executing admin command: {command}")
        response = await matrix_bot.send_admin_command(command, timeout=30, expected_response_pattern=expected_pattern)
        logger.info(f"Command successful: {response[:200]}")
        return {"success": True, "response": response}
    except TimeoutError as e:
        logger.error(f"Timeout executing command: {e}")
        return {"success": False, "error": f"Command timed out: {str(e)}"}
    except Exception as e:
        logger.error(f"Admin command failed: {e}")
        return {"success": False, "error": str(e)}

@router.post("/ban_room")
async def ban_room(
    room_id: str = Form(...),
    auth_token: str = Depends(verify_admin_auth)
):
    """Ban a specific room."""
    command = f"!admin rooms moderation ban-room {room_id}"
    # Look for success message (adjust based on what your bot actually returns)
    result = await send_matrix_admin_command(command, expected_pattern=r"(banned|successfully)")
    logger.info(f"Ban room {room_id}: {result}")
    return JSONResponse(result)

@router.post("/ban_user")
async def ban_user(
    user_id: str = Form(...),
    auth_token: str = Depends(verify_admin_auth)
):
    """Deactivate a specific user."""
    command = f"!admin users deactivate {user_id}"
    # Look for "has been deactivated" in response
    result = await send_matrix_admin_command(command, expected_pattern=r"has been deactivated")
    logger.info(f"Ban user {user_id}: {result}")
    return JSONResponse(result)

@router.post("/ban_users_bulk")
async def ban_users_bulk(
    user_ids: str = Form(...),
    auth_token: str = Depends(verify_admin_auth)
):
    """Deactivate multiple users at once."""
    users = json.loads(user_ids)
    users_formatted = "\n".join(users)
    command = f"!admin users deactivate-all\n```\n{users_formatted}\n```"
    # Look for deactivation confirmation
    result = await send_matrix_admin_command(command, expected_pattern=r"deactivated")
    logger.info(f"Bulk ban {len(users)} users: {result}")
    return JSONResponse(result)
