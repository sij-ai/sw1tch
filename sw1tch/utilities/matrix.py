import asyncio
import time
import re
from typing import List, Dict, Union, Optional
from fastapi import HTTPException
from nio import AsyncClient, RoomMessageText, RoomMessageNotice

from sw1tch import config, logger

# Persistent Matrix Bot
class PersistentMatrixBot:
    def __init__(self):
        self.client = None
        self.connected = False
        self.lock = asyncio.Lock()
    
    async def ensure_connected(self):
        async with self.lock:
            if not self.connected or not self.client:
                matrix_config = config["matrix_admin"]
                self.client = AsyncClient(config["base_url"], matrix_config["username"])
                login_response = await self.client.login(matrix_config["password"])
                if getattr(login_response, "error", None):
                    raise Exception(f"Login error: {login_response.error}")
                await self.client.join(matrix_config["room"])
                await self.client.sync(timeout=5000)
                self.connected = True
                logger.info("Matrix bot connected")
    
    async def send_admin_command(self, command: str) -> str:
        await self.ensure_connected()
        admin_room = config["matrix_admin"]["room"]
        admin_user = config["matrix_admin"]["super_admin"]
        
        # Get current sync token
        sync_response = await self.client.sync(timeout=1000)
        next_batch = sync_response.next_batch
        
        # Send command
        await self.client.room_send(
            room_id=admin_room,
            message_type="m.room.message",
            content={"msgtype": "m.text", "body": command}
        )
        
        query_time = time.time()
        timeout = 10
        start = time.time()
        
        while (time.time() - start) < timeout:
            sync = await self.client.sync(timeout=2000, since=next_batch)
            next_batch = sync.next_batch
            room = sync.rooms.join.get(admin_room)
            if room and room.timeline and room.timeline.events:
                message_events = [e for e in room.timeline.events if isinstance(e, (RoomMessageText, RoomMessageNotice))]
                for event in message_events:
                    event_time = event.server_timestamp / 1000.0
                    if event.sender == admin_user and event_time >= query_time:
                        return event.body
            await asyncio.sleep(0.5)
        
        raise TimeoutError("No response from admin bot")

# Global instance
matrix_bot = PersistentMatrixBot()

def parse_response(response_text: str, query: str) -> Dict[str, Union[str, List[str]]]:
    query_parts = query.strip().split()
    array_key = query_parts[0] if query_parts else "data"
    codeblock_pattern = r"(.*?):\s*\n```\s*\n([\s\S]*?)\n```"
    match = re.search(codeblock_pattern, response_text)
    if match:
        message = match.group(1).strip()
        items = [line for line in match.group(2).split('\n') if line.strip()]
        return {"message": message, array_key: items}
    return {"response": response_text}

async def get_matrix_users() -> List[str]:
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
                message_events = [e for e in room.timeline.events if isinstance(e, (RoomMessageText, RoomMessageNotice))]
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

async def deactivate_user(user: str) -> bool:
    matrix_config = config["matrix_admin"]
    homeserver = config["base_url"]
    username = matrix_config.get("username")
    password = matrix_config.get("password")
    admin_room = matrix_config.get("room")
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

async def get_matrix_rooms(page: int) -> List[Dict[str, Union[str, int]]]:
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
        logger.debug(f"Fetching rooms for page {page}")
        await client.join(admin_room)
        initial_sync = await client.sync(timeout=5000)
        next_batch = initial_sync.next_batch
        command = f"!admin rooms list-rooms {page} --exclude-banned --exclude-disabled"
        await client.room_send(
            room_id=admin_room,
            message_type="m.room.message",
            content={"msgtype": "m.text", "body": command},
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
                message_events = [e for e in room.timeline.events if isinstance(e, (RoomMessageText, RoomMessageNotice))]
                for event in message_events:
                    event_time = event.server_timestamp / 1000.0
                    if event.
