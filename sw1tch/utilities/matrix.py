import asyncio
import time
import re
from typing import List, Dict, Union, Optional
from fastapi import HTTPException
from nio import AsyncClient, RoomMessageText, RoomMessageNotice

from sw1tch import config, logger

# Persistent Matrix Bot with improved connection handling
class PersistentMatrixBot:
    def __init__(self):
        self.client = None
        self.connected = False
        self.lock = asyncio.Lock()
        self.last_activity = None
    
    async def ensure_connected(self, force_reconnect: bool = False):
        """Ensure bot is connected, optionally forcing a reconnection."""
        async with self.lock:
            # Force reconnect if requested
            if force_reconnect and self.connected:
                logger.info("Forcing reconnection...")
                await self._disconnect()
            
            # Check if connection is stale (no activity for 5 minutes)
            if self.connected and self.last_activity:
                time_since_activity = time.time() - self.last_activity
                if time_since_activity > 300:
                    logger.info(f"Connection stale ({time_since_activity:.0f}s since last activity), reconnecting...")
                    await self._disconnect()
            
            # Connect if not connected
            if not self.connected or not self.client:
                await self._connect()
    
    async def _connect(self):
        """Internal method to establish connection."""
        try:
            matrix_config = config["matrix_admin"]
            self.client = AsyncClient(config["base_url"], matrix_config["username"])
            
            login_response = await self.client.login(matrix_config["password"])
            if getattr(login_response, "error", None):
                raise Exception(f"Login error: {login_response.error}")
            
            await self.client.join(matrix_config["room"])
            
            # Do initial sync
            sync_response = await self.client.sync(timeout=5000)
            if getattr(sync_response, "error", None):
                raise Exception(f"Sync error: {sync_response.error}")
            
            self.connected = True
            self.last_activity = time.time()
            logger.info("Matrix bot connected successfully")
            
        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            self.connected = False
            self.client = None
            raise
    
    async def _disconnect(self):
        """Internal method to disconnect."""
        if self.client:
            try:
                await self.client.logout()
                await self.client.close()
            except Exception as e:
                logger.warning(f"Error during disconnect: {e}")
        self.client = None
        self.connected = False
    
    async def send_admin_command(self, command: str, timeout: int = 30, expected_response_pattern: str = None) -> str:
        """
        Send admin command and wait for response.
        
        Args:
            command: The admin command to send
            timeout: How long to wait for response (seconds)
            expected_response_pattern: Regex pattern to match in response (optional)
        
        Returns:
            Response text from admin bot
        """
        # Ensure we're connected (force reconnect to ensure fresh connection)
        await self.ensure_connected(force_reconnect=False)
        
        admin_room = config["matrix_admin"]["room"]
        admin_user = config["matrix_admin"]["super_admin"]
        
        try:
            # Get current sync state
            sync_response = await self.client.sync(timeout=1000)
            if getattr(sync_response, "error", None):
                logger.error(f"Sync error before sending command: {sync_response.error}")
                # Try to reconnect
                await self.ensure_connected(force_reconnect=True)
                sync_response = await self.client.sync(timeout=1000)
            
            next_batch = sync_response.next_batch
            query_time = time.time()
            
            # Send command
            logger.info(f"Sending command: {command}")
            send_response = await self.client.room_send(
                room_id=admin_room,
                message_type="m.room.message",
                content={"msgtype": "m.text", "body": command}
            )
            
            # Check if send was successful
            if getattr(send_response, "error", None):
                logger.error(f"Failed to send command: {send_response.error}")
                raise Exception(f"Failed to send command: {send_response.error}")
            
            logger.debug(f"Command sent successfully, waiting for response (timeout: {timeout}s)")
            
            # Wait for response
            start = time.time()
            while (time.time() - start) < timeout:
                try:
                    sync = await self.client.sync(timeout=2000, since=next_batch)
                    if getattr(sync, "error", None):
                        logger.warning(f"Sync error while waiting: {sync.error}")
                        await asyncio.sleep(1)
                        continue
                    
                    next_batch = sync.next_batch
                    room = sync.rooms.join.get(admin_room)
                    
                    if room and room.timeline and room.timeline.events:
                        message_events = [e for e in room.timeline.events 
                                        if isinstance(e, (RoomMessageText, RoomMessageNotice))]
                        
                        for event in message_events:
                            event_time = event.server_timestamp / 1000.0
                            if event.sender == admin_user and event_time >= query_time:
                                response_body = event.body
                                self.last_activity = time.time()
                                logger.info(f"Got response after {time.time() - start:.1f}s: {response_body[:100]}...")
                                
                                # If we have an expected pattern, check for it
                                if expected_response_pattern:
                                    if re.search(expected_response_pattern, response_body, re.IGNORECASE):
                                        return response_body
                                    else:
                                        logger.warning(f"Response doesn't match expected pattern: {expected_response_pattern}")
                                        # Continue waiting for a better match
                                        continue
                                
                                return response_body
                    
                    await asyncio.sleep(0.5)
                
                except Exception as sync_error:
                    logger.error(f"Error during sync loop: {sync_error}")
                    # Try to recover
                    await asyncio.sleep(1)
            
            logger.error(f"Timeout waiting for response to: {command}")
            raise TimeoutError(f"No response from admin bot within {timeout}s")
        
        except Exception as e:
            logger.error(f"Error in send_admin_command: {e}")
            # Mark as disconnected so next call will reconnect
            self.connected = False
            raise

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
                    if event.sender == admin_response_user and event_time >= query_time:
                        response_message = event.body
                        logger.debug(f"Found rooms response: {response_message[:100]}...")
                        break
                if response_message:
                    break
        await client.logout()
        await client.close()
        if not response_message:
            raise HTTPException(status_code=504, detail="No response from admin user within timeout")
        
        # Parse the response
        parsed = parse_response(response_message, "rooms list-rooms")
        room_pattern = r"(!\S+)\s+Members: (\d+)\s+Name: (.*)"
        rooms = []
        for line in parsed.get("rooms", []):
            match = re.match(room_pattern, line)
            if match:
                room_id, members, name = match.groups()
                rooms.append({
                    "room_id": room_id,
                    "members": int(members),
                    "name": name.strip()
                })
        return rooms
    except Exception as e:
        await client.close()
        logger.error(f"Error fetching rooms for page {page}: {e}")
        raise HTTPException(status_code=500, detail=f"Error fetching rooms: {e}")

async def get_room_members(room_id: str, local_only: bool = False) -> Dict[str, Union[str, int, List[Dict[str, str]]]]:
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
        logger.debug(f"Fetching members for room {room_id}")
        await client.join(admin_room)
        initial_sync = await client.sync(timeout=5000)
        next_batch = initial_sync.next_batch
        command = f"!admin rooms info list-joined-members {room_id}"
        if local_only:
            command += " --local-only"
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
                    if event.sender == admin_response_user and event_time >= query_time:
                        response_message = event.body
                        logger.debug(f"Found members response: {response_message[:100]}...")
                        break
                if response_message:
                    break
        await client.logout()
        await client.close()
        if not response_message:
            raise HTTPException(status_code=504, detail="No response from admin user within timeout")
        
        # Parse the response
        parsed = parse_response(response_message, "members list-joined-members")
        member_pattern = r"(@\S+)\s*\|\s*(\S+)"
        members = []
        message_match = re.match(r"(\d+) Members in Room \"(.*)\":", parsed["message"])
        total_members = int(message_match.group(1)) if message_match else 0
        room_name = message_match.group(2) if message_match else room_id
        for line in parsed.get("members", []):
            match = re.match(member_pattern, line)
            if match:
                user_id, display_name = match.groups()
                members.append({"user_id": user_id, "display_name": display_name})
        return {
            "room_id": room_id,
            "room_name": room_name,
            "total_members": total_members,
            "local_members": members
        }
    except Exception as e:
        await client.close()
        logger.error(f"Error fetching members for room {room_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error fetching room members: {e}")

def check_banned_room_name(room_name: str) -> bool:
    """Check if a room name matches any regex pattern in config/room-ban-regex.txt."""
    try:
        with open("sw1tch/config/room-ban-regex.txt", "r") as f:
            patterns = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        for pattern in patterns:
            if re.search(pattern, room_name, re.IGNORECASE):
                logger.debug(f"Room name '{room_name}' matches banned pattern '{pattern}'")
                return True
        return False
    except FileNotFoundError:
        logger.warning("room-ban-regex.txt not found; no rooms will be considered banned")
        return False
    except Exception as e:
        logger.error(f"Error reading room-ban-regex.txt: {e}")
        return False

def get_matched_pattern(room_name: str) -> str:
    """Return the regex pattern that matched the room name, or empty string."""
    try:
        with open("sw1tch/config/room-ban-regex.txt", "r") as f:
            patterns = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        for pattern in patterns:
            if re.search(pattern, room_name, re.IGNORECASE):
                return pattern
        return ""
    except:
        return ""
