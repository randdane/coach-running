# Getting Started with Your Homelab Running Coach

Welcome! This guide will walk you through setting up your own self-hosted, AI-powered running coach. By the end of this tutorial, you'll have a system that greets you every morning with a training update and reviews your Strava runs automatically using local or cloud-based LLMs.

---

## Prerequisites

Before we begin, ensure you have the following ready:

1.  **A Homelab Host:** A machine running Linux (Ubuntu, Debian, or even a Raspberry Pi 4/5) with [Docker and Docker Compose](https://docs.docker.com/get-docker/) installed.
2.  **A Strava Account:** You'll need this to sync your activities.
3.  **Tailscale (Recommended):** We use [Tailscale Funnel](https://tailscale.com/kb/1223/funnel/) to securely expose your Strava webhook endpoint to the internet without opening ports on your router.
4.  **An LLM Provider:** An API key for OpenAI (default model `gpt-5.3-chat-latest`) or Anthropic (Claude), OR a local [Ollama](https://ollama.com/) instance. The model is wired up in `litellm/config.yaml` — swap it there if you want a different one.

---

## Step 1: Clone and Scaffold

First, grab the code and prepare your environment.

```bash
git clone https://github.com/randdane/coach-running.git
cd coach-running

# Create your local data directory
mkdir -p data/memory data/backups data/ntfy

# Create your environment file
cp .env.example .env
```

---

## Step 2: Configure the Strava API

Strava needs to know how to talk to your coach.

1.  Go to the [Strava API Settings](https://www.strava.com/settings/api).
2.  Create an application. Set the "Authorization Callback Domain" to `localhost`.
3.  Note your **Client ID** and **Client Secret**.

4.  **Find your Athlete ID:** Visit your Strava profile in a browser. The numeric ID is in the URL: `https://www.strava.com/athletes/<ATHLETE_ID>`.

5.  **Get your Initial Refresh Token** by doing a one-time OAuth exchange:

    a. Open this URL in your browser (replace `YOUR_CLIENT_ID`):
    ```
    https://www.strava.com/oauth/authorize?client_id=YOUR_CLIENT_ID&response_type=code&redirect_uri=http://localhost&approval_prompt=force&scope=activity:read_all
    ```
    b. Authorize the app. You'll be redirected to a `localhost` URL. Nothing is listening on port 80, so your browser will show a "can't connect" error — that's expected. Just copy the `code=` value out of the address bar.

    c. Exchange the code for a refresh token:
    ```bash
    curl -X POST https://www.strava.com/oauth/token \
      -F client_id=YOUR_CLIENT_ID \
      -F client_secret=YOUR_CLIENT_SECRET \
      -F code=YOUR_CODE \
      -F grant_type=authorization_code
    ```
    Copy the `refresh_token` from the response.

6.  Update your `.env` file:
    ```env
    ATHLETE_ID=your_numeric_athlete_id
    STRAVA_CLIENT_ID=your_id
    STRAVA_CLIENT_SECRET=your_secret
    STRAVA_REFRESH_TOKEN=your_refresh_token
    ```

---

## Step 3: Configure Your Environment

Open `.env` and set the remaining required values:

```env
# Your local timezone — controls when the morning cron fires
TZ=America/New_York

# A strong random key for the LiteLLM proxy (do not leave as default)
LITELLM_MASTER_KEY=change-me

# Your LLM API key (at least one required)
OPENAI_API_KEY=sk-...
# ANTHROPIC_API_KEY=sk-ant-...

# The model to use (must match a model_name in litellm/config.yaml)
COACH_MODEL=gpt-5.3-chat-latest
```

To generate a secure `LITELLM_MASTER_KEY`:
```bash
openssl rand -hex 32
```

---

## Step 4: Set Up the Webhook Tunnel

Strava sends a "ping" whenever you finish a run. Since your coach is in your homelab, we need a way for Strava to find it.

1.  Install [Tailscale](https://tailscale.com/download) on your host and sign in:
    ```bash
    sudo tailscale up
    ```
2.  Enable **Funnel** for your tailnet in the [Tailscale admin console](https://login.tailscale.com/admin/dns) under *DNS → HTTPS Certificates* and *Access controls → Funnel*. Funnel is off by default and won't work until you turn it on.
3.  Expose port 8000 to the public internet:
    ```bash
    sudo tailscale funnel 8000
    ```
4.  Your public URL will be `https://<your-node-name>.<your-tailnet-name>.ts.net`. The command prints it on startup.
5.  Generate a random secret for your webhook path:
    ```bash
    openssl rand -hex 16
    ```
6.  Set `WEBHOOK_SECRET` in your `.env` to that value.
7.  Register the webhook subscription with Strava (do this after the coach is running in Step 5):
    ```bash
    curl -X POST https://www.strava.com/api/v3/push_subscriptions \
      -F client_id=YOUR_CLIENT_ID \
      -F client_secret=YOUR_CLIENT_SECRET \
      -F callback_url=https://<your-node-name>.<your-tailnet-name>.ts.net/webhook/strava/<WEBHOOK_SECRET> \
      -F verify_token=STRAVA
    ```
    `verify_token` is an arbitrary string Strava echoes back during the handshake — the coach doesn't validate it, so `STRAVA` is a fine default. You should get back a `{"id": ...}` confirming the subscription.

---

## Step 5: Launch!

Now, pull the trigger and start the services.

```bash
docker compose up -d
```

Check the logs to make sure everything is happy:
```bash
docker compose logs -f coach-app
```

Visit `http://localhost:8000` (or your Tailscale IP) in your browser. You should see the Coach Dashboard!

---

## Step 6: Set Up Push Notifications

The coach sends morning check-ins and post-run reviews to your phone via [ntfy](https://ntfy.sh/).

1.  Install the **ntfy** app on your phone ([Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy) / [iOS](https://apps.apple.com/app/ntfy/id1625396347)).
2.  In the app, add a new server pointing to your host's Tailscale IP or LAN address: `http://<tailscale-ip-or-lan-ip>:8080`. (The compose file binds ntfy to all interfaces — if you'd rather keep it private, edit `docker-compose.yml` to bind `127.0.0.1:8080` and front ntfy with `tailscale serve 8080` instead.)
3.  Subscribe to the topic that matches `NTFY_TOPIC` in your `.env` (default: `coach`).

You can test it immediately with:
```bash
curl -d "Coach is live!" http://localhost:8080/coach
```

---

## Step 7: Seed Your Coach's Memory

An AI coach is only as good as the context it has. Navigate to the **Memory** and **Plan** tabs in the web UI:

1.  **Athlete Context:** Tell the coach about yourself. "I am 35, my max heart rate is 185, I prefer morning runs, and I'm currently recovering from a light calf strain."
2.  **Training Plan:** Paste in your current plan. "Monday: Rest, Tuesday: 5km Tempo, Wednesday: 8km Easy..."

The coach will use these files to tailor its advice.

---

## Making the Most of Your Coach

### The Morning Check-in
Every morning at 6:00 AM (configurable via `MORNING_CRON` in `.env`), you'll receive a notification on your phone with a summary of what's on deck for today based on your plan and recent performance.

### Post-Run Reviews
Minutes after you finish a run, your coach will analyze the data. It looks at your heart rate, pace, and consistency. It might say, *"Great job on those intervals, but your heart rate was a bit high for 'Easy'—let's keep the effort lower tomorrow to stay fresh."*

A nightly sync job (`POLL_CRON`, default 10:30 PM) also fetches recent Strava activities to catch anything a missed webhook may have skipped.

### Manual Triggers
If you want a check-in *right now*, just hit the **"Run morning check-in"** button on the dashboard.

---

## What's Next?

*   **Custom Voice:** Edit `prompts/coach_voice.md` to change how your coach speaks. Want a drill sergeant? A supportive friend? A data-obsessed scientist? Just change the prompt.
*   **Go Local:** Uncomment the `ollama` section in `docker-compose.yml` and the corresponding entries in `litellm/config.yaml` to run your coach entirely on your own hardware without sending data to the cloud.
*   **Expansion:** Want to track bike rides or swims? The system is built on a modular SQLite backend—feel free to extend the `activities` table!

Happy running!

---

## Resources

* This repo is based on the talk by Adam Gordon Bell during PyTexas 2026.
  * https://www.pytexas.org/2026/schedule/talks/#i-built-an-ai-running-coach-that-actually-remembers-my-training
  * https://youtu.be/EfIbH20J97Q


