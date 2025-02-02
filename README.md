# Matrix Registration System

A FastAPI-based web application that manages Matrix account registration requests for homeservers that do not offer SMTP authentication (like conduwuit). It provides a registration token to users via email, with automatic token rotation and various safety features.

## Features

- Daily rotating registration tokens
- Rate limiting per email address
- Multiple account restrictions
- IP and email address banning
- Username pattern banning with regex support
- Automatic downtime before token rotation
- Gruvbox-themed UI with responsive design

## Setup

1. Install dependencies:
```bash
pip install fastapi uvicorn jinja2 httpx pyyaml python-multipart
```

2. Configure your settings:
```bash
cp config.yaml.example config.yaml
# Edit config.yaml with your settings
```

3. Create required files:
```bash
touch banned_ips.txt banned_emails.txt banned_usernames.txt
mkdir static
# Add your logo.png to static/
# Add favicon.ico to static/
```

4. Generate initial registration token:
```bash
openssl rand -base64 32 | tr -d '/+=' | head -c 32 > .registration_token
```

## Configuration

The `config.yaml` file supports these options:

```yaml
port: 6626
homeserver: "your.server"
token_reset_time_utc: 0        # 24-hour format (e.g., 0 = 00:00 UTC)
downtime_before_token_reset: 30 # minutes
email_cooldown: 3600           # seconds between requests per email
multiple_users_per_email: false # allow multiple accounts per email?

smtp:
  host: "smtp.example.com"
  port: 587
  username: "your@email.com"
  password: "yourpassword"
  use_tls: true
```

## Token Rotation

Add this to your crontab to rotate the registration token daily at 00:00 UTC:

```bash
# Edit crontab with: crontab -e
0 0 * * * openssl rand -base64 32 | tr -d '/+=' | head -c 32 > /path/to/your/.registration_token
```

## Running the Server

Development:
```bash
python registration.py
```

Production:
```bash
uvicorn registration:app --host 0.0.0.0 --port 6626
```

## Security Features

- **IP Banning**: Add IPs to `banned_ips.txt`, one per line
- **Email Banning**: Add emails to `banned_emails.txt`, one per line
- **Username Patterns**: Add regex patterns to `banned_usernames.txt`, one per line
- **Registration Tracking**: All requests are logged to `registrations.json`


## Security Notes

- Place behind a reverse proxy with HTTPS
- Consider placing the registration token file outside web root
- Regularly backup `registrations.json`
- Monitor logs for abuse patterns
