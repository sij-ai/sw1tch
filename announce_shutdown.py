#!/usr/bin/env python3
import sys
import smtplib
import time
from email.message import EmailMessage

# Import from your existing codebase
sys.path.append('/home/sij/hand_of_morpheus')
from sw1tch import config, load_registrations

# --- CONFIGURATION ---
SHUTDOWN_DATE = "January 22, 2026"
SUBJECT = f"IMPORTANT: {config['homeserver']} Server Shutdown Notice"
DELAY_SECONDS = 10  # Slow down to keep the mail server happy

# Emails that already received the message (copied from your logs)
SKIP_EMAILS = {
    "emanuel.sch@nderl.es",
    "wotmail8811@gmail.com",
    "isaac.dong@hotmail.com",
    "2idx7@ptct.net",
    "Mafktv@bk.ru",
    "ardumont@duck.com",
    "qm_00@outlook.com",
    "faptwister666@gmail.com",
    "wife-ethanol-flop@duck.com",
    "sij@sij.law",
    "yuect0314@tempmail.cn",
    "iceking@tuta.io",
    "yuect1984@tempmail.cn",
    "antoine@antoine.com.br",
    "dedieamber@ptct.net",
    "copper132@ptct.net",
    "mgrace5350@gmail.com",
    "varied-gating-line@duck.com",
    "kyxatuqe@logsmarter.net",
    "Onetwo421@proton.me"
}

BODY = f"""
Hello,

You are receiving this email because you have an account on {config['homeserver']}.

This is a notice that the {config['homeserver']} Matrix homeserver will be permanently sunset on {SHUTDOWN_DATE}.

WHAT THIS MEANS:
1. You have 30 days to export your data or migrate to a new homeserver.
2. New registrations are now closed.
3. On {SHUTDOWN_DATE}, all data will be permanently deleted.

HOW TO MIGRATE:
- Matrix does not currently support full account migration (moving your MXID).
- You will need to register a new account on a different server.
- You can use tools like 'matrix-migration-tool' to move your room memberships.

Thank you for being part of our community.

Regards,
{config['homeserver']} Administration
"""

def send_announcement():
    registrations = load_registrations()
    print(f"Loaded {len(registrations)} registrations.")
    
    smtp_conf = config["email"]["smtp"]
    
    try:
        server = smtplib.SMTP(smtp_conf["host"], smtp_conf["port"])
        if smtp_conf.get("use_tls", True):
            server.starttls()
        server.login(smtp_conf["username"], smtp_conf["password"])
        print("SMTP Connection successful.")
    except Exception as e:
        print(f"SMTP Failed: {e}")
        return

    count = 0
    skipped = 0
    
    for user in registrations:
        email_addr = user.get('email')
        username = user.get('requested_name')
        
        # Skip invalid or already sent emails
        if not email_addr or "null@nope.no" in email_addr:
            continue
            
        if email_addr in SKIP_EMAILS:
            skipped += 1
            continue
            
        msg = EmailMessage()
        msg.set_content(BODY)
        msg["Subject"] = SUBJECT
        msg["From"] = smtp_conf["from"]
        msg["To"] = email_addr
        
        try:
            server.send_message(msg)
            print(f"[{count+1}] Sent to {username} ({email_addr})")
            count += 1
            # Wait to avoid rate limits
            time.sleep(DELAY_SECONDS)
        except Exception as e:
            print(f"FAILED to send to {email_addr}: {e}")
            # If connection dropped, try to reconnect? 
            # For simplicity, we just print fail. You can re-run script adding this email to SKIP list.

    server.quit()
    print(f"\nCompleted. Sent {count} emails. Skipped {skipped} already sent.")

if __name__ == "__main__":
    print(f"Found {len(SKIP_EMAILS)} emails already sent.")
    confirm = input(f"Resume sending to remaining users with {DELAY_SECONDS}s delay? Type 'YES': ")
    if confirm == "YES":
        send_announcement()
    else:
        print("Aborted.")
