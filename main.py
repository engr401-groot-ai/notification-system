import os
import smtplib
import logging
import requests
from datetime import datetime
import pytz
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
from collections import defaultdict
from googleapiclient.discovery import build
from google.cloud import firestore

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("notifier")

# Load environment variables
load_dotenv()
logger.info("Loaded environment variables.")

def load_firestore_config():
    """
    Fetch dynamic settings (sender, password, recipients) from Firestore
    and inject them into os.environ so the rest of the module can read them.
    """
    try:
        if not os.getenv("GCP_PROJECT_ID") and not os.path.exists(".env"):
            logger.info("Skipping Firestore load (No Project ID and no .env file).")
            return

        logger.info("Attempting to load notification settings from Firestore...")
        db = firestore.Client(database="notification-system")
        doc_ref = db.collection("settings").document("configuration")
        doc = doc_ref.get()

        if not doc.exists:
            logger.warning("Firestore document 'settings/configuration' not found.")
            return

        data = doc.to_dict() or {}
        
        if "sender" in data:
            os.environ["SENDER"] = str(data["sender"])

        if "password" in data:
            os.environ["PASSWORD"] = str(data["password"])

        if "recipients" in data and isinstance(data["recipients"], list):
            os.environ["RECIPIENTS"] = ",".join(data["recipients"])

        logger.info("✓ Successfully loaded settings from Firestore (settings/configuration).")

    except Exception as e:
        logger.warning(f"Failed to load Firestore config: {e}")

load_firestore_config()

SERVER_API_URL = os.getenv("SERVER_API_URL", "")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
SMTP_SERVER = os.getenv("SMTP_SERVER", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", ""))
SENDER = os.getenv("SENDER")
PASSWORD = os.getenv("PASSWORD")
RECIPIENTS = os.getenv("RECIPIENTS", "")
HOURS_BACK = int(os.getenv("HOURS_BACK", ""))

def validate_configuration() -> None:
    """Validate that all required environment variables are set."""
    required = {
        "SERVER_API_URL": SERVER_API_URL,
        "SENDER": SENDER,
        "PASSWORD": PASSWORD,
        "RECIPIENTS": RECIPIENTS,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        msg = "Missing required env vars: " + ", ".join(missing)
        logger.error(msg)
        raise RuntimeError(msg)

def fetch_recent_mentions():
    """Fetch recent mentions from the scraper API."""
    try:
        url = f"{SERVER_API_URL}/api/mentions/recent?hours={HOURS_BACK}"
        logger.info(f"Fetching mentions from {url}...")
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()
        logger.info(f"Found {data.get('count', 0)} mentions.")
        return data.get("results", [])
    except Exception as e:
        logger.error(f"Failed to fetch mentions: {e}")
        return []

def fetch_video_metadata(video_urls):
    """Fetch video metadata from YouTube API including published dates."""
    if not video_urls or not YOUTUBE_API_KEY:
        if not YOUTUBE_API_KEY:
            logger.warning("YOUTUBE_API_KEY not configured. Video dates will show as N/A.")
        return {}
    
    try:
        # Extract video IDs from URLs
        video_ids = []
        url_to_id = {}
        for url in video_urls:
            # Extract video ID from URL (format: https://www.youtube.com/watch?v=VIDEO_ID)
            if 'v=' in url:
                video_id = url.split('v=')[1].split('&')[0]
                video_ids.append(video_id)
                url_to_id[video_id] = url
        
        if not video_ids:
            return {}
        
        # Build YouTube API client
        youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)
        
        # Fetch video details in batches (API allows up to 50 IDs per request)
        video_metadata = {}
        batch_size = 50
        
        for i in range(0, len(video_ids), batch_size):
            batch_ids = video_ids[i:i+batch_size]
            
            logger.info(f"Fetching metadata for {len(batch_ids)} videos from YouTube API...")
            
            request = youtube.videos().list(
                part='snippet,liveStreamingDetails',
                id=','.join(batch_ids)
            )
            response = request.execute()
            
            # Parse response
            for item in response.get('items', []):
                video_id = item['id']
                video_url = url_to_id.get(video_id)
                
                if video_url:
                    snippet = item.get('snippet', {})
                    live_details = item.get('liveStreamingDetails', {})
                    
                    # Determine the best available date
                    # Priority: actualStartTime > scheduledStartTime > publishedAt
                    published_at = live_details.get('actualStartTime')
                    if not published_at:
                        published_at = live_details.get('scheduledStartTime')
                    if not published_at:
                        published_at = snippet.get('publishedAt', '')
                        
                    video_metadata[video_url] = {
                        'title': snippet.get('title', ''),
                        'publishedAt': published_at,
                        'channelTitle': snippet.get('channelTitle', '')
                    }
        
        logger.info(f"Retrieved metadata for {len(video_metadata)} videos from YouTube API")
        return video_metadata
        
    except Exception as e:
        logger.warning(f"Failed to fetch video metadata from YouTube API: {e}")
        return {}

def format_timestamp(seconds):
    """Format seconds into MM:SS or HH:MM:SS timestamp."""
    hours = int(seconds) // 3600
    minutes = (int(seconds) % 3600) // 60
    secs = int(seconds) % 60
    
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    else:
        return f"{minutes:02d}:{secs:02d}"

def parse_video_date(published_at):
    """Parse YouTube publishedAt date to Hawaii timezone display string."""
    if not published_at:
        return "N/A"
    
    try:
        dt = datetime.fromisoformat(published_at.replace('Z', '+00:00'))
        hawaii_tz = pytz.timezone('Pacific/Honolulu')
        dt_hawaii = dt.astimezone(hawaii_tz)
        return dt_hawaii.strftime("%m/%d/%Y @ %I:%M %p")
    except:
        return "N/A"
        
def format_email_body(mentions, video_metadata=None):
    """Format the mentions into an HTML email body."""
    if not mentions:
        return None

    # Group mentions by video URL
    videos = defaultdict(list)
    for m in mentions:
        video_url = m.get("video_url", "")
        videos[video_url].append(m)
    
    # Sort each video's mentions by timestamp (ascending)
    for video_url in videos:
        videos[video_url].sort(key=lambda x: x.get("start_sec", 0))
    
    # Sort videos by published date (descending - newest first)
    def video_sort_key(video_item):
        video_url, _ = video_item
        
        # Use YouTube API metadata for sorting
        if video_metadata and video_url in video_metadata:
            published_at = video_metadata[video_url].get('publishedAt')
            if published_at:
                try:
                    dt = datetime.fromisoformat(published_at.replace('Z', '+00:00'))
                    # Sort by date (descending - newest day first)
                    # But within same day, sort by time (ascending - earliest first)
                    # Return tuple: (-date, time) where date is negative for descending
                    date_only = dt.date()
                    time_only = dt.time()
                    # Convert date to ordinal (days since epoch) and negate for descending
                    return (-date_only.toordinal(), time_only)
                except:
                    pass
        
        # If no date available, put at the end
        return (0, datetime.max.time())
    
    sorted_videos = sorted(videos.items(), key=video_sort_key)

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
                    <th>Hearing / Briefing</th>
                    <th>Keyword</th>
                    <th>Text</th>
                    <th>Timestamp</th>
                    <th>Video Date</th>
                </tr>
            </thead>
            <tbody>
    """
    
    # Iterate through sorted videos and their sorted mentions
    for video_url, video_mentions in sorted_videos:
        # Get video name from the first mention
        video_name = video_mentions[0].get("video_name", "Unknown Video") if video_mentions else "Unknown Video"
        
        # Get video published date from metadata (calculate once per video)
        video_date_display = "N/A"
        if video_metadata and video_url in video_metadata:
            published_at = video_metadata[video_url].get('publishedAt')
            video_date_display = parse_video_date(published_at)
        
        for m in video_mentions:
            keyword = m.get("keyword", "N/A")
            text = m.get("text", "")
            link = m.get("link", "#")
            start_sec = m.get("start_sec", 0)

            # Truncate text if too long
            if len(text) > 200:
                text = text[:197] + "..."

            # Format timestamp
            timestamp = format_timestamp(start_sec)

            html += f"""
                <tr>
                    <td>{video_name}</td>
                    <td><strong>{keyword}</strong></td>
                    <td>{text}</td>
                    <td><a href="{link}">{timestamp}</a></td>
                    <td>{video_date_display}</td>
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
    if not SENDER or not PASSWORD or not RECIPIENTS:
        logger.warning("Email configuration missing. Skipping email send.")
        return

    recipients = [e.strip() for e in RECIPIENTS.split(",") if e.strip()]
    if not recipients:
        logger.warning("No recipients configured.")
        return

    msg = MIMEMultipart()
    msg["From"] = SENDER
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "html"))

    try:
        logger.info(f"Connecting to SMTP server {SMTP_SERVER}:{SMTP_PORT}...")
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SENDER, PASSWORD)
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

    # Extract unique video URLs from mentions
    video_urls = list(set(m.get("video_url", "") for m in mentions if m.get("video_url")))
    
    # Fetch video metadata from YouTube API
    video_metadata = fetch_video_metadata(video_urls)

    # Format and send email
    body = format_email_body(mentions, video_metadata)
    if body:
        subject = f"GRO Office's House and Senate YouTube Scraper: {len(mentions)} New Mentions Found!"
        send_email(subject, body)
    
    logger.info("Job completed.")

if __name__ == "__main__":
    main()
