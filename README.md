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

1. Clone the repo:
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
openssl rand -hex 16 > .registration_token
```

6. Set up token rotation:
```bash
# Copy and configure the token refresh script
cp example-refresh_token.sh refresh_token.sh
nano refresh_token.sh  # configure paths for your environment

# Make it executable
chmod +x refresh_token.sh

# Add to crontab (runs at midnight UTC)
crontab -e
# Add this line:
0 0 * * * /path/to/your/hand_of_morpheus/refresh_token.sh 2>&1
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

The included `refresh_token.sh` script handles both token rotation and conduwuit container management. Review and adjust its settings before use.
