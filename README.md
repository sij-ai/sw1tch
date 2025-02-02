# Matrix Registration System

A FastAPI-based web application that manages Matrix account registration requests for homeservers that do not offer SMTP authentication (like conduwuit). It provides a registration token to users via email, with automatic token rotation and various safety features.

Currently in use for the [We2.ee](https://we2.ee/about) homeserver, at [join.we2.ee](https://join.we2.ee)

## Features

- Daily rotating registration tokens
- Rate limiting per email address
- Multiple account restrictions
- IP and email address banning
- Username pattern banning with regex support
- Automatic downtime before token rotation
- Gruvbox-themed UI with responsive design

## Setup

1. Clone the repo
```bash
git clone https://sij.ai/sij/hand_of_morpheus
cd hand_of_morpheus
```

2. Install dependencies:
```bash
pip install fastapi uvicorn jinja2 httpx pyyaml python-multipart
```

3. Configure your settings:
```bash
cp example-config.yaml config.yaml
nano config.yaml
```

4. Create required files:
```bash
touch banned_ips.txt banned_emails.txt banned_usernames.txt

# Optionally, copy the anti-CSAM example-banned_usernames.txt
cp example-banned_usernames.txt banned_usernames.txt
```

Add your logo.png to `static/logo.png`
Add favicon.ico to `static/favicon.ico`

5. Generate initial registration token:
```bash
openssl rand -base64 32 | tr -d '/+=' | head -c 32 > .registration_token
```

## Configuration

The `config.yaml` file supports these options:

```yaml
port: 6626
homeserver: "your.server"
token_reset_time_utc: 0          # 24-hour format (e.g., 0 = 00:00 UTC)
downtime_before_token_reset: 30  # minutes
email_cooldown: 3600             # seconds between requests per email
multiple_users_per_email: false  # allow multiple accounts per email?

smtp:
  host: "smtp.example.com"
  port: 587
  username: "your@email.com"
  password: "yourpassword"
  use_tls: true
```

You can also customize the subject and body of the email that is sent.

## Token Rotation

Add this to your crontab to rotate the registration token daily at 00:00 UTC:

```bash
# Edit crontab with: crontab -e
0 0 * * * openssl rand -base64 32 | tr -d '/+=' | head -c 32 > /path/to/hand_of_morpheus/.registration_token
```

## Running the Server

```bash
python registration.py
```

Consider running in a `tmux` session, or creating a system service for it.

## Security Features

- **IP Banning**: Add IPs to `banned_ips.txt`, one per line
- **Email Banning**: Add emails to `banned_emails.txt`, one per line
- **Username Patterns**: Add regex patterns to `banned_usernames.txt`, one per line; consider including the anti-CSAM entries in `example-banned_usernames.txt` as a starting point
- **Registration Tracking**: All requests are logged to `registrations.json`

## Security Notes

- Place behind a reverse proxy with HTTPS
- Consider placing the registration token file outside web root
- Regularly backup `registrations.json`
- Monitor logs for abuse patterns

## Example Conduwuit docker run command

```bash
docker run -d \
  -p 127.0.0.1:8448:6167 \
  -v db:/var/lib/conduwuit/ \
  -v /path/to/hand_of_morpheus/.registration_token:/registration_token:ro \
  -e CONDUWUIT_SERVER_NAME="your.domain" \
  -e CONDUWUIT_DATABASE_PATH="/var/lib/conduwuit/conduwuit.db" \
  -e CONDUWUIT_DATABASE_BACKUP_PATH="/var/lib/conduwuit/backup" \
  -e CONDUWUIT_ALLOW_REGISTRATION=true \
  -e CONDUWUIT_REGISTRATION_TOKEN_FILE="/registration_token" \
  -e CONDUWUIT_PORT=6167 \
  -e CONDUWUIT_ADDRESS="0.0.0.0" \
  -e CONDUWUIT_NEW_USER_DISPLAYNAME_SUFFIX="" \
  -e CONDUWUIT_ALLOW_PUBLIC_ROOM_DIRECTORY_OVER_FEDERATION=true \
  -e CONDUWUIT_ALLOW_PUBLIC_ROOM_DIRECTORY_WITHOUT_AUTH=true \
  -e CONDUWUIT_ALLOW_FEDERATION=true \
  -e CONDUWUIT_AUTO_JOIN_ROOMS='["#community:your.domain","#welcome:your.domain"]' \
  --name conduwuit \
  --restart unless-stopped \
  ghcr.io/girlbossceo/conduwuit:v0.5.0-rc2-e5049cae4a3890dc5f61ead53281f23b36bf4c97
  ```