import os
import subprocess
import requests
import feedparser
import datetime
from typing import List
from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from sw1tch import BASE_DIR, config, logger, verify_admin_auth
from sw1tch.utilities.matrix import AsyncClient

router = APIRouter(prefix="/_admin/warrant_canary", dependencies=[Depends(verify_admin_auth)])
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

ATTESTATIONS_FILE = os.path.join(BASE_DIR, "config", "attestations.txt")
CANARY_OUTPUT_FILE = os.path.join(BASE_DIR, "data", "canary.txt")
TEMP_CANARY_FILE = os.path.join(BASE_DIR, "data", "temp_canary_message.txt")

def load_attestations():
    try:
        with open(ATTESTATIONS_FILE, 'r') as f:
            return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail=f"Attestations file not found: {ATTESTATIONS_FILE}")

def get_nist_time():
    session = requests.Session()
    retry_strategy = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    endpoints = [
        "https://timeapi.io/api/Time/current/zone?timeZone=UTC",
        "https://worldtimeapi.org/api/timezone/UTC",
    ]
    for url in endpoints:
        try:
            response = session.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            if "dateTime" in data:
                return data["dateTime"] + " UTC"
            elif "utc_datetime" in data:
                return data["utc_datetime"] + " UTC"
        except requests.RequestException:
            pass
    raise HTTPException(status_code=500, detail="Failed to fetch NIST time")

def get_rss_headline():
    rss_config = config['canary'].get('rss', {})
    rss_url = rss_config.get('url', 'https://www.democracynow.org/democracynow.rss')
    feed = feedparser.parse(rss_url)
    if feed.entries and len(feed.entries) > 0:
        return {"title": feed.entries[0].title, "link": feed.entries[0].link}
    raise HTTPException(status_code=500, detail="Failed to fetch RSS headline")

def get_bitcoin_latest_block():
    try:
        response = requests.get("https://blockchain.info/latestblock", timeout=10)
        if response.status_code == 200:
            data = response.json()
            block_response = requests.get(f"https://blockchain.info/rawblock/{data['hash']}", timeout=10)
            if block_response.status_code == 200:
                block_data = block_response.json()
                hash_str = data["hash"].lstrip("0") or "0"
                return {
                    "height": data["height"],
                    "hash": hash_str,
                    "time": datetime.datetime.fromtimestamp(block_data["time"]).strftime("%Y-%m-%d %H:%M:%S UTC")
                }
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch Bitcoin block data")

def create_warrant_canary_message(attestations: List[str], note: str):
    nist_time = get_nist_time()
    rss_data = get_rss_headline()
    bitcoin_block = get_bitcoin_latest_block()
    org = config['canary']['organization']
    admin_name = config['canary'].get('admin_name', 'Admin')
    admin_title = config['canary'].get('admin_title', 'administrator')
    message = f"{org} Warrant Canary · {nist_time}\n"
    message += f"I, {admin_name}, the {admin_title} of {org}, state this {datetime.datetime.now().strftime('%dth day of %B, %Y')}:\n"
    for i, attestation in enumerate(attestations, 1):
        message += f"  {i}. {org} {attestation}\n"
    if note:
        message += f"\nNOTE: {note}\n"
    message += "\nDatestamp Proof:\n"
    message += f"  Daily News:  \"{rss_data['title']}\"\n"
    message += f"  Source URL:  {rss_data['link']}\n"
    message += f"  BTC block:   #{bitcoin_block['height']}, {bitcoin_block['time']}\n"
    message += f"  Block hash:  {bitcoin_block['hash']}\n"
    return message.rstrip() + "\n"

def sign_with_gpg(message: str, gpg_key_id: str, passphrase: str):
    try:
        with open(TEMP_CANARY_FILE, "w", newline='\n') as f:
            f.write(message)
        cmd = ["gpg", "--batch", "--yes", "--passphrase", passphrase, "--clearsign", "--default-key", gpg_key_id, TEMP_CANARY_FILE]
        subprocess.run(cmd, check=True)
        with open(f"{TEMP_CANARY_FILE}.asc", "r") as f:
            signed_message = f.read()
        os.remove(TEMP_CANARY_FILE)
        os.remove(f"{TEMP_CANARY_FILE}.asc")
        lines = signed_message.splitlines()
        signature_idx = next(i for i, line in enumerate(lines) if line == "-----BEGIN PGP SIGNATURE-----")
        if lines[signature_idx + 1] == "":
            lines.pop(signature_idx + 1)
        return "\n".join(lines)
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"GPG signing failed: {e}")

async def post_to_matrix(signed_message: str):
    try:
        matrix = config['canary']['credentials']
        client = AsyncClient(config['base_url'], matrix['username'])
        await client.login(matrix['password'])
        full_message = (
            f"This is the {config['canary']['organization']} Warrant Canary, signed with GPG for authenticity. "
            "Copy the code block below to verify with `gpg --verify`:\n\n"
            f"```\n{signed_message}\n```"
        )
        content = {
            "msgtype": "m.text",
            "body": full_message,
            "format": "org.matrix.custom.html",
            "formatted_body": (
                f"This is the {config['canary']['organization']} Warrant Canary, signed with GPG for authenticity. "
                "Copy the code block below to verify with <code>gpg --verify</code>:<br><br>"
                f"<pre>{signed_message}</pre>"
            )
        }
        await client.room_send(config['matrix_admin']['room'], "m.room.message", content)
        await client.logout()
        await client.close()
        return True
    except Exception as e:
        logger.error(f"Error posting to Matrix: {e}")
        return False

@router.get("/", response_class=HTMLResponse)
async def warrant_canary_form(request: Request):
    attestations = load_attestations()
    return templates.TemplateResponse("canary_form.html", {
        "request": request,
        "attestations": attestations,
        "organization": config["canary"]["organization"]
    })

@router.post("/preview", response_class=HTMLResponse)
async def warrant_canary_preview(request: Request, attestations: List[str] = Form(...), note: str = Form(default="")):
    message = create_warrant_canary_message(attestations, note)
    return templates.TemplateResponse("canary_preview.html", {"request": request, "message": message})

@router.post("/sign", response_class=HTMLResponse)
async def warrant_canary_sign(request: Request, message: str = Form(...), passphrase: str = Form(...)):
    signed_message = sign_with_gpg(message, config["canary"]["gpg_key_id"], passphrase)
    with open(CANARY_OUTPUT_FILE, "w") as f:
        f.write(signed_message)
    return templates.TemplateResponse("canary_success.html", {"request": request, "signed_message": signed_message})

@router.post("/post", response_class=JSONResponse)
async def warrant_canary_post(signed_message: str = Form(...)):
    success = await post_to_matrix(signed_message)
    return JSONResponse({"message": "Posted to Matrix" if success else "Failed to post to Matrix"})
