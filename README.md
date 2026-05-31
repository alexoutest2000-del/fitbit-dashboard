# Fitbit Dashboard

**Modern web dashboard for Fitbit health data** — auto-refreshing metrics and charts via the Google Health API.

![screenshot](https://img.shields.io/badge/status-beta-yellow)

## What It Shows

Every metric Fitbit tracks, pulled automatically from Google Health:

| Metric | Icon | Unit |
|--------|------|------|
| Steps | 👣 | steps |
| Heart Rate | ❤️ | bpm |
| Active Zone Minutes | 🔥 | minutes |
| Calories | ⚡ | kcal |
| Weight | ⚖️ | kg |
| Body Fat | 📊 | % |
| Distance | 📏 | km |
| Floors | 🏢 | floors |
| Activity Level | 🏃 | — |
| Blood Glucose | 🩸 | mg/dL |
| Hydration | 💧 | ml |
| Sedentary Periods | 🪑 | minutes |

Each metric gets a **today card** and a **30-day trend chart**. One click to refresh.

## Setup

### 1. Prerequisites
- Python 3.10+
- A Google account with Fitbit data migrated to Google Health

### 2. Google Cloud Project
1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a project (e.g., `fitbit-dashboard`)
3. **Enable the Google Health API**
   - APIs & Services → Library → search "Google Health API" → Enable
4. **Create OAuth credentials**
   - APIs & Services → Credentials → Create Credentials → OAuth client ID
   - Application type: **Web application**
   - Authorized redirect URI: `http://localhost:8080/oauth/callback`
   - Save your **Client ID** and **Client Secret**
5. **Configure consent screen**
   - APIs & Services → OAuth consent screen
   - User Type: **External**
   - Add scope: `https://www.googleapis.com/auth/health`
   - Add your email as a test user

### 3. Install & Configure
```bash
git clone https://github.com/alexoutest2000-del/fitbit-dashboard.git
cd fitbit-dashboard
bash run.sh
```

On first run, it creates `config.yaml`. Edit it with your credentials:
```yaml
google_client_id: "123456789-xxxxx.apps.googleusercontent.com"
google_client_secret: "GOCSPX-xxxxx"
redirect_uri: "http://localhost:8080/oauth/callback"
```

Run again: `bash run.sh`

### 4. Sign In
Open `http://localhost:8080` → ⚙ Settings → enter credentials → **Sign in with Google**.

The dashboard loads your metrics automatically.

## Architecture

```
Flask (Python)          — single-file backend, no database
  ├── OAuth 2.0 + PKCE  — Authorization Code flow with Proof Key
  ├── Google Health API  — dailyRollUp endpoint for clean summaries
  └── Embedded SPA       — vanilla HTML/CSS/JS with Chart.js

config.yaml              — OAuth credentials (gitignored)
tokens.json              — OAuth tokens, auto-refreshed (gitignored)
server.py                — everything else
```

## Security

- **No secrets in code** — credentials via config.yaml (gitignored)
- **OAuth 2.0 with PKCE** — protects against authorization code interception
- **State parameter validation** — prevents CSRF attacks
- **Token auto-refresh** — uses refresh tokens; no re-login needed
- **Local storage only** — tokens stay on your machine
- **No third-party tracking** — zero analytics, zero telemetry

## Pricing

**Free.** Google Health API has no cost for personal use. Quota: 86.4 million requests/day (1,000/sec).

## Development

```bash
# Install deps
pip install -r requirements.txt

# Run directly
python3 server.py

# Or use the idempotent launcher (handles venv + port cleanup)
bash run.sh
```

## Data Source

Fitbit's legacy API is deprecated. All Fitbit data now routes through the **Google Health API**, which provides real-time access to every metric without manual exports.

The `dailyRollUp` endpoint returns pre-aggregated daily values — no raw data wrangling needed.

## License

MIT
