# Notification System

Email notification system for the GRO Office's House and Senate YouTube Scraper. This Cloud Run Job fetches recent mentions from the scraper API and sends HTML email notifications.

---

## Quick Start

### Local Development

1. **Install dependencies**:
   ```bash
   pip3 install -r requirements.txt
   ```

2. **Configure `.env` file**:
   ```bash
   SCRAPER_API_URL=" "
   SMTP_SERVER=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USER=your-email@hawaii.edu
   SMTP_PASSWORD=yourapp-passwordhere
   TO_EMAILS=recipient1@hawaii.edu,recipient2@hawaii.edu,...
   HOURS_BACK=24
   ```

3. **Run locally**:
   ```bash
   python3 main.py
   ```

---

## Gmail SMTP Setup

### Step 1: Enable 2-Factor Authentication
1. Go to https://myaccount.google.com/security
2. Enable **2-Step Verification**

### Step 2: Create App Password
1. Go to https://myaccount.google.com/security
2. Click **2-Step Verification** → **App passwords**
3. Select **Mail** and **Other (Custom name)**
4. Name it: `YouTube Scraper Notifier`
5. **Copy the 16-character password** (remove spaces!)

### Step 3: Update `.env` inside local directory
```bash
SMTP_USER=your-email@hawaii.edu
SMTP_PASSWORD=yourapppasswordhere
TO_EMAILS=recipient1@hawaii.edu,recipient2@hawaii.edu,...
```

---

## Google Cloud Deployment

### Step 1: Create Secret in Secret Manager

1. Go to [Secret Manager](https://console.cloud.google.com/security/secret-manager) in Google Cloud Console
2. Select project: `your-project-id`
3. Click **CREATE SECRET**
4. Configure:
   - **Name**: `notification-system-env`
   - **Secret value**: Paste your entire `.env` file contents
5. Click **CREATE SECRET**
6. Click on the secret name → **PERMISSIONS** tab
7. Click **GRANT ACCESS**
   - **New principals**: `your-project-id-compute@developer.gserviceaccount.com`
   - **Role**: `Secret Manager Secret Accessor`
8. Click **SAVE**

### Step 2: Push to GitHub (Triggers Cloud Build)

```bash
git add .
git commit -m "Deploy notification system"
git push origin main
```

This automatically triggers Cloud Build which will:
1. Build the Docker image
2. Push to Artifact Registry: `us-west1-docker.pkg.dev/its-gro/youtube-transcript-scraper-repo/notification-system:latest`

Wait for the build to complete (check [Cloud Build History](https://console.cloud.google.com/cloud-build/builds)).

### Step 3: Create Cloud Run Job

1. Go to [Cloud Run](https://console.cloud.google.com/run) in Google Cloud Console
2. Select project: `your-project-id`
3. Click **CREATE JOB**
4. Configure:
   - **Job name**: `notifier-job`
   - **Region**: `us-central1`
   - **Container image URL**: Click **SELECT** → Choose `notification-system:latest` from Artifact Registry
5. Click **CONTAINER, VARIABLES & SECRETS, CONNECTIONS, SECURITY**
6. Under **Variables & Secrets** tab:
   - Click **ADD VARIABLE**
   - **Name**: `GCP_PROJECT_ID`
   - **Value**: `your-project-id`
7. Under **Security** tab:
   - **Service account**: `your-project-id-compute@developer.gserviceaccount.com`
8. Click **CREATE**

### Step 4: Test the Job

1. Go to [Cloud Run Jobs](https://console.cloud.google.com/run/jobs)
2. Click on `notifier-job`
3. Click **EXECUTE**
4. Monitor the execution logs
5. Check your email for the notification

---

## Updating the Deployment

### Update Code
```bash
# Make your changes
git add .
git commit -m "Update notification logic"
git push origin main
```

Cloud Build will automatically rebuild. Then:

1. Go to [Cloud Run Jobs](https://console.cloud.google.com/run/jobs)
2. Click on `notifier-job`
3. Click **EDIT**
4. Click **SELECT** under Container image URL
5. Choose the new `notification-system:latest` image
6. Click **DEPLOY**

### Update Secrets

1. Go to [Secret Manager](https://console.cloud.google.com/security/secret-manager)
2. Click on `notification-system-env`
3. Click **NEW VERSION**
4. Paste updated `.env` contents
5. Click **ADD NEW VERSION**

---

## Common Issues

**"Missing required env vars"**
- Check that all required variables are in Secret Manager
- Verify the service account has `Secret Manager Secret Accessor` role

**"SMTP Authentication failed"**
- Verify you're using an App Password, not your regular password
- Check for typos in the password
- Ensure 2FA is enabled on your Google account

**"404 Not Found" from API**
- Verify `SCRAPER_API_URL` is correct
- Test the API endpoint: `curl https://your-api-url/list_mentions?hours=24`