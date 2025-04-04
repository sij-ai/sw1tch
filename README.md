# Sw1tch: Matrix Registration and Admin System for Conduwuit

`Sw1tch` is a FastAPI-based web application designed to enhance the `conduwuit` Matrix homeserver by addressing key shortcomings for public deployments. It manages account registration with email-based token requests and provides an admin API by relaying HTTP requests to a Matrix admin room, parsing responses for automation. Currently in use for the [We2.ee](https://we2.ee/about) homeserver at [join.we2.ee](https://join.we2.ee).

This project is specifically built around `conduwuit`, an excellent Matrix homeserver that lacks native SMTP authentication and a robust admin API—issues `sw1tch` resolves elegantly.

## Features

- Daily rotating registration tokens, emailed upon request
- Email-based registration requiring a valid address
- Rate limiting per email address
- IP, email, and regex-based username banning
- Automatic downtime before token rotation
- Admin API via Matrix room message relaying
- Warrant canary generation and posting (work in progress)
- Gruvbox-themed, responsive UI

## Setup

1. **Clone the Repository**:
   ```bash
   git clone https://sij.ai/sij/hand_of_morpheus
   cd hand_of_morpheus
   ```

2. **Install Dependencies**:
   ```bash
   pip install fastapi uvicorn jinja2 httpx pyyaml python-multipart nio requests feedparser urllib3 smtplib
   ```

3. **Set Up Configuration**:
   ```bash
   cp -r ./sw1tch/example-config ./sw1tch/config
   nano sw1tch/config/config.yaml
   ```
   - `config.yaml`: Fill in credentials and options for registration, Matrix admin, canary, and SMTP.
   - `conduwuit.env`: Add `conduwuit` settings (see [Conduwuit Config Examples](https://conduwuit.puppyirl.gay/configuration/examples.html)).
   - `banned_emails.txt`: Prefilled with disposable email providers linked to spam/abuse.
   - `banned_usernames.txt`: Prefilled with regex patterns targeting CSAM-related usernames.
   - `banned_ips.txt`: Blank; add IPs to block token requests.
   - `attestations.txt`: Generic statements for the warrant canary; customize as needed.

4. **Add Static Assets**:
   ```bash
   # Add your logo and favicon to the static directory
   cp your-logo.png sw1tch/static/logo.png
   cp your-favicon.ico sw1tch/static/favicon.ico
   ```

5. **Generate Initial Registration Token**:
   ```bash
   openssl rand -hex 16 > sw1tch/data/.registration_token
   ```

6. **Configure `launch.sh`**:
   - `launch.sh` manages token rotation, `conduwuit` container updates, and ensures the `sw1tch` service runs:
     - Updates the `conduwuit` Docker image from a Nix-built repository.
     - Refreshes the registration token and restarts the container.
     - Starts or restarts the `sw1tch` FastAPI service.
   ```bash
   nano launch.sh  # Adjust paths (e.g., BASE_PATH, REPO_PATH) for your environment
   chmod +x launch.sh
   ```

7. **Set Up Cron Jobs**:
   ```bash
   crontab -e
   ```
   Add:
   ```bash
   # Daily token refresh and container restart at midnight UTC
   0 0 * * * cd /home/sij/hand_of_morpheus && ./launch.sh --refresh-token > /home/sij/hand_of_morpheus/logs/token_refresh.log 2>&1

   # Weekly conduwuit update (Sundays at 2 AM UTC)
   0 2 * * 0 cd /home/sij/hand_of_morpheus && ./launch.sh --update --force-restart > /home/sij/hand_of_morpheus/logs/update.log 2>&1

   # Ensure service runs after reboot
   @reboot cd /home/sij/hand_of_morpheus && ./launch.sh > /home/sij/hand_of_morpheus/logs/reboot.log 2>&1
   ```

## Running the Server

Run manually:
```bash
./launch.sh # --refresh-token, --super-admin, --update, and/or --force-restart
```

### launch.sh Command line flags

1. **`--refresh-token`**:
   - **Purpose**: Generates a new, random 6-character hexadecimal registration token and writes it to `sw1tch/data/.registration_token`.
   - **Behavior**: Overwrites the existing token, logs the new value, and exits on failure (e.g., if the file isn’t writable).
   - **When to Use**: 
     - Daily via cron (e.g., at midnight UTC) to rotate tokens as a security measure.
     - Manually if you suspect the current token has been compromised.
   - **Example**: `./launch.sh --refresh-token`

2. **`--super-admin`**:
   - **Purpose**: Generates a random 16-character emergency password for the `@conduit` user in `conduwuit` and passes it to the container via `CONDUWUIT_EMERGENCY_PASSWORD`.
   - **Behavior**: Logs the username (`@conduit:we2.ee`) and password, which you can use to log in and regain admin access.
   - **When to Use**: 
     - During initial setup to establish admin access.
     - If you lose access to the admin account and need to recover it.
   - **Example**: `./launch.sh --super-admin`

3. **`--update`**:
   - **Purpose**: Updates the `conduwuit` Docker image by pulling the latest source from `REPO_PATH`, building it with Nix, and tagging it as `conduwuit:custom`.
   - **Behavior**: Requires Git and Nix; exits on failure (e.g., if the build fails or no image is produced).
   - **When to Use**: 
     - Weekly via cron to keep `conduwuit` up-to-date with the latest features or fixes.
     - Manually when you want to apply a specific update.
   - **Example**: `./launch.sh --update`

4. **`--force-restart`**:
   - **Purpose**: Forces the `sw1tch` registration service to restart by killing any process on the configured port (from `config.yaml`) and starting a new instance.
   - **Behavior**: Removes the PID file, starts `python3 -m sw1tch` detached, and verifies it’s running; logs errors if it fails to start.
   - **When to Use**: 
     - After updating `sw1tch` code or configuration to ensure changes take effect.
     - If the service is unresponsive or stuck.
     - Combined with `--update` to refresh everything.
   - **Example**: `./launch.sh --force-restart`

### Additional Notes
- **Combination**: Flags can be combined (e.g., `./launch.sh --update --force-restart`) for comprehensive updates.
- **Default Behavior**: Without flags, the script restarts the `conduwuit` container and ensures `sw1tch` is running (no forced restart).
- **Cron Integration**: The comments align with your crontab (daily `--refresh-token`, weekly `--update --force-restart`, reboot startup).

## Security Features

- **IP Banning**: Add IPs to `sw1tch/config/banned_ips.txt`.
- **Email Banning**: Add emails to `sw1tch/config/banned_emails.txt`.
- **Username Patterns**: Add regex to `sw1tch/config/banned_usernames.txt`.
- **Registration Tracking**: Logged to `sw1tch/data/registrations.json`.
- **Admin API**: Relays HTTP requests to `#admins` room, parsing responses.

## Security Notes

- Use a reverse proxy (e.g., Nginx) with HTTPS.
- Move `.registration_token` outside the web root if exposed.
- Backup `sw1tch/data/registrations.json` regularly.
- Monitor `sw1tch/logs/registration.log` for abuse.

## Warrant Canary

The warrant canary feature (in progress) generates signed statements posted to a Matrix room, using data from RSS feeds and Bitcoin blocks for freshness. Configure in `config.yaml` under `canary`. Current limitations include UI polish and broader testing.

## Conduwuit Integration

`Sw1tch` resolves two `conduwuit` shortcomings:
1. **Email-Based Registration**: Requires a valid email for token requests, enhancing security for public homeservers.
2. **Admin API**: Bridges HTTP requests to Matrix room messages, enabling automation by parsing `@conduit` responses.

Review `launch.sh` for `conduwuit` container management settings.
