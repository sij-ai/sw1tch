from fastapi import APIRouter, Form, Depends
from fastapi.responses import JSONResponse
from datetime import datetime, timedelta
import httpx
import re

from sw1tch import config, logger, load_registrations, save_registrations, verify_admin_auth
from sw1tch.utilities.matrix import get_matrix_users, deactivate_user

router = APIRouter(prefix="/_admin", dependencies=[Depends(verify_admin_auth)])

@router.post("/purge_unfulfilled_registrations", response_class=JSONResponse)
async def purge_unfulfilled_registrations(min_age_hours: int = Form(default=24)):
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

@router.post("/deactivate_undocumented_users", response_class=JSONResponse)
async def deactivate_undocumented_users():
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
async def retroactively_document_users():
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
