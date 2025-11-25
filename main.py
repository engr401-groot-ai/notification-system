import os
import smtplib
import logging
import requests
from datetime import datetime
import pytz
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("notifier")

# Load environment variables
def load_environment_variables() -> None:
    """Load environment variables from Secret Manager (Cloud Run) or .env (local)."""
    project_id = os.getenv("GCP_PROJECT_ID", "its-gro")
    secret_name = f"projects/{project_id}/secrets/notification-system-env/versions/latest"

    try:
        from google.cloud import secretmanager
        client = secretmanager.SecretManagerServiceClient()
        response = client.access_secret_version(request={"name": secret_name})
        payload = response.payload.data.decode("utf-8")

        for line in payload.strip().splitlines():
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()

        logger.info("✓ Loaded environment variables from Secret Manager")
        return

    except Exception as e:
        logger.info(f"Secret Manager load failed ({e}); falling back to .env")
        load_dotenv()
        logger.info("✓ Loaded environment variables from .env (if present)")

load_environment_variables()

# Configuration - Load from environment
SCRAPER_API_URL = os.getenv("SCRAPER_API_URL", "")
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
TO_EMAILS = os.getenv("TO_EMAILS", "")
HOURS_BACK = int(os.getenv("HOURS_BACK", "24"))

def validate_configuration() -> None:
    """Validate that all required environment variables are set."""
    required = {
        "SCRAPER_API_URL": SCRAPER_API_URL,
        "SMTP_USER": SMTP_USER,
        "SMTP_PASSWORD": SMTP_PASSWORD,
        "TO_EMAILS": TO_EMAILS,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        msg = "Missing required env vars: " + ", ".join(missing)
        logger.error(msg)
        raise RuntimeError(msg)

def fetch_recent_mentions():
    """Fetch recent mentions from the scraper API."""
    try:
        url = f"{SCRAPER_API_URL}?hours={HOURS_BACK}"
        logger.info(f"Fetching mentions from {url}...")
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()
        logger.info(f"Found {data.get('count', 0)} mentions.")
        return data.get("results", [])
    except Exception as e:
        logger.error(f"Failed to fetch mentions: {e}")
        return []

def format_email_body(mentions):
    """Format the mentions into an HTML email body."""
    if not mentions:
        return None

    html = """
    <html>
    <head>
        <style>
            body { font-family: Arial, sans-serif; line-height: 1.6; color: #333; }
            h2 { color: #2c3e50; }
            table { width: 100%; border-collapse: collapse; margin-top: 20px; }
            th, td { padding: 12px; border: 1px solid #ddd; text-align: left; }
            th { background-color: #f4f4f4; color: #333; }
            tr:nth-child(even) { background-color: #f9f9f9; }
            a { color: #3498db; text-decoration: none; }
            a:hover { text-decoration: underline; }
            .footer { margin-top: 30px; font-size: 0.9em; color: #777; }
        </style>
    </head>
    <body>
        <h2>New mentions found in the last 24 hours:</h2>
        <table>
            <thead>
                <tr>
                    <th>Video Title</th>
                    <th>Keyword</th>
                    <th>Text Snippet</th>
                    <th>Timestamp</th>
                    <th>Found At</th>
                </tr>
            </thead>
            <tbody>
    """
    
    for m in mentions:
        video_name = m.get("video_name", "Unknown Video")
        keyword = m.get("keyword", "N/A")
        text = m.get("text", "")
        link = m.get("link", "#")
        start_sec = m.get("start_sec", 0)
        created_at = m.get("created_at", "")

        # Truncate text if too long
        if len(text) > 200:
            text = text[:197] + "..."

        # Format timestamp (MM:SS or HH:MM:SS)
        hours = int(start_sec) // 3600
        minutes = (int(start_sec) % 3600) // 60
        seconds = int(start_sec) % 60
        
        if hours > 0:
            timestamp = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        else:
            timestamp = f"{minutes:02d}:{seconds:02d}"

        # Format created_at to Hawaii time
        try:
            if created_at:
                dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                hawaii_tz = pytz.timezone('Pacific/Honolulu')
                dt_hawaii = dt.astimezone(hawaii_tz)
                created_display = dt_hawaii.strftime("%b %d, %I:%M %p HST")
            else:
                created_display = "N/A"
        except Exception:
            created_display = str(created_at)

        html += f"""
                <tr>
                    <td>{video_name}</td>
                    <td><strong>{keyword}</strong></td>
                    <td>{text}</td>
                    <td><a href="{link}">{timestamp}</a></td>
                    <td>{created_display}</td>
                </tr>
        """

    html += """
            </tbody>
        </table>
        <div class="footer">
            <p>This is an automated notification from the GRO Office's House and Senate YouTube Scraper.</p>
        </div>
    </body>
    </html>
    """
    return html

def send_email(subject, body):
    """Send an HTML email using SMTP."""
    if not SMTP_USER or not SMTP_PASSWORD or not TO_EMAILS:
        logger.warning("SMTP configuration missing. Skipping email send.")
        return

    recipients = [e.strip() for e in TO_EMAILS.split(",") if e.strip()]
    if not recipients:
        logger.warning("No recipients configured.")
        return

    msg = MIMEMultipart()
    msg["From"] = SMTP_USER
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "html"))

    try:
        logger.info(f"Connecting to SMTP server {SMTP_SERVER}:{SMTP_PORT}...")
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        logger.info(f"Email sent successfully to {len(recipients)} recipients.")
    except Exception as e:
        logger.error(f"Failed to send email: {e}")

def main():
    """Main entry point for the notification job."""
    logger.info("Starting notification job...")
    
    # Validate configuration
    validate_configuration()
    
    # Fetch mentions
    mentions = fetch_recent_mentions()
    
    if not mentions:
        logger.info("No new mentions found. Exiting.")
        return

    # Format and send email
    body = format_email_body(mentions)
    if body:
        subject = f"GRO Office's House and Senate YouTube Scraper: {len(mentions)} New Mentions Found!"
        send_email(subject, body)
    
    logger.info("Job completed.")

if __name__ == "__main__":
    main()
