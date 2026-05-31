#!/usr/bin/env python3
"""
Fitbit Dashboard — Modern web dashboard for Fitbit health data via Google Health API.
Single-file Flask app with inline HTML/CSS/JS.
"""
import os
import json
import hashlib
import base64
import secrets
import time
import socket
import yaml
import requests
from datetime import datetime, timedelta
from urllib.parse import urlencode, parse_qs
from flask import Flask, request, redirect, jsonify, send_from_directory

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)

# ── Config ──────────────────────────────────────────────────────────────────
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
TOKENS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tokens.json")

def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)

def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        yaml.safe_dump(cfg, f, default_flow_style=False)

def load_tokens():
    if os.path.exists(TOKENS_PATH):
        with open(TOKENS_PATH) as f:
            return json.load(f)
    return {}

def save_tokens(tokens):
    with open(TOKENS_PATH, "w") as f:
        json.dump(tokens, f, indent=2)

# ── OAuth 2.0 Helpers ───────────────────────────────────────────────────────
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
SCOPES = [
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
    "https://www.googleapis.com/auth/googlehealth.location.readonly",
    "https://www.googleapis.com/auth/googlehealth.ecg.readonly",
    "https://www.googleapis.com/auth/googlehealth.irn.readonly",
    "openid",
    "https://www.googleapis.com/auth/userinfo.profile",
]

def generate_pkce():
    """Generate PKCE code verifier and challenge."""
    code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return code_verifier, code_challenge

def get_auth_url(cfg):
    """Build Google OAuth authorization URL."""
    verifier, challenge = generate_pkce()
    params = {
        "client_id": cfg["google_client_id"],
        "redirect_uri": cfg["redirect_uri"],
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": secrets.token_hex(16),
    }
    return GOOGLE_AUTH_URL + "?" + urlencode(params), verifier, params["state"]

def exchange_code(cfg, code, code_verifier):
    """Exchange authorization code for tokens."""
    resp = requests.post(GOOGLE_TOKEN_URL, data={
        "client_id": cfg["google_client_id"],
        "client_secret": cfg["google_client_secret"],
        "code": code,
        "code_verifier": code_verifier,
        "redirect_uri": cfg["redirect_uri"],
        "grant_type": "authorization_code",
    }, timeout=15)
    return resp.json() if resp.ok else {"error": resp.text}

def refresh_access_token(cfg, refresh_token):
    """Refresh an expired access token."""
    resp = requests.post(GOOGLE_TOKEN_URL, data={
        "client_id": cfg["google_client_id"],
        "client_secret": cfg["google_client_secret"],
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }, timeout=15)
    if resp.ok:
        data = resp.json()
        return data.get("access_token")
    return None

def get_valid_token(cfg):
    """Get a valid access token, refreshing if needed."""
    tokens = load_tokens()
    access_token = tokens.get("access_token")
    expires_at = tokens.get("expires_at", 0)
    refresh_token = tokens.get("refresh_token")

    if access_token and time.time() < expires_at - 60:
        return access_token, None

    if refresh_token:
        new_token = refresh_access_token(cfg, refresh_token)
        if new_token:
            tokens["access_token"] = new_token
            tokens["expires_at"] = time.time() + 3500
            save_tokens(tokens)
            return new_token, None
        else:
            return None, "Refresh token expired. Please re-authenticate."

    return None, "Not authenticated. Please sign in with Google."

# ── Google Health API Client ────────────────────────────────────────────────
HEALTH_API_BASE = "https://health.googleapis.com/v4"

# Data types and their friendly names (Health API uses simple hyphenated names)
DATA_TYPES = {
    "steps": ("Steps", "👣", "steps"),
    "heart-rate": ("Heart Rate", "❤️", "bpm"),
    "active-minutes": ("Active Zone Minutes", "🔥", "min"),
    "calories-in-heart-rate-zone": ("Calories", "⚡", "kcal"),
    "weight": ("Weight", "⚖️", "kg"),
    "body-fat": ("Body Fat", "📊", "%"),
    "distance": ("Distance", "📏", "km"),
    "floors": ("Floors", "🏢", "floors"),
    "sleep": ("Sleep", "😴", "hours"),
    "blood-glucose": ("Blood Glucose", "🩸", "mg/dL"),
    "oxygen-saturation": ("Oxygen Saturation", "🫁", "%"),
    "respiratory-rate": ("Respiratory Rate", "🫁", "breaths/min"),
    "body-temperature": ("Body Temperature", "🌡️", "°C"),
    "blood-pressure": ("Blood Pressure", "💓", "mmHg"),
}

def fetch_daily_rollup(access_token, data_type, days=30):
    """Fetch daily rollup data for a given data type via gRPC Transcoding POST."""
    today = datetime.now()
    end_dt = today + timedelta(days=1)  # exclusive end
    start_dt = today - timedelta(days=days)

    url = f"{HEALTH_API_BASE}/users/me/dataTypes/{data_type}/dataPoints:dailyRollUp"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    body = {
        "range": {
            "start": {"date": {"year": start_dt.year, "month": start_dt.month, "day": start_dt.day}},
            "end": {"date": {"year": end_dt.year, "month": end_dt.month, "day": end_dt.day}},
        }
    }

    try:
        resp = requests.post(url, headers=headers, json=body, timeout=15)
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 400 and "Invalid data type" in resp.text:
            return {"error": "invalid_type", "message": f"'{data_type}' is not a recognised data type"}
        elif resp.status_code == 404:
            return {"error": "no_data", "message": f"No data for {data_type}"}
        else:
            return {"error": "api_error", "message": resp.text[:200]}
    except Exception as e:
        return {"error": "network_error", "message": str(e)}

def parse_rollup_value(point):
    """Extract the main numeric value from a DailyRollupDataPoint.

    The value is a union field keyed by rollup type (e.g. 'steps', 'heartRate', 'calories').
    """
    value = point.get("value", {})
    if not value:
        return None

    # The union field name varies by data type — try common patterns
    for field_name, field_val in value.items():
        if isinstance(field_val, dict):
            # Look for avg/min/max/sum aggregation fields
            for agg in ["avg", "average", "sum", "total", "value"]:
                if agg in field_val:
                    v = field_val[agg]
                    if isinstance(v, (int, float)):
                        return v
            # Maybe direct numeric fields
            for k, v in field_val.items():
                if isinstance(v, (int, float)) and k not in ("confidence",):
                    return v
    return None

def format_date_from_civil(civil_time):
    """Convert a CivilDateTime dict to 'YYYY-MM-DD' string."""
    d = civil_time.get("date", {})
    return f"{d.get('year',0):04d}-{d.get('month',0):02d}-{d.get('day',0):02d}"

def fetch_all_data(access_token, days=30):
    """Fetch all available data types."""
    results = {}
    for data_type, (name, icon, unit) in DATA_TYPES.items():
        raw = fetch_daily_rollup(access_token, data_type, days)
        if "error" in raw:
            results[data_type] = {"name": name, "icon": icon, "unit": unit, "error": raw["error"], "message": raw.get("message", ""), "points": []}
        else:
            points = []
            for dp in raw.get("dailyRollupDataPoints", []):
                date_str = format_date_from_civil(dp.get("civilStartTime", {}))
                val = parse_rollup_value(dp)
                if date_str and val is not None:
                    points.append({"date": date_str, "value": round(val, 2)})
            points.sort(key=lambda p: p["date"])
            today_val = points[-1]["value"] if points else None
            results[data_type] = {
                "name": name,
                "icon": icon,
                "unit": unit,
                "points": points,
                "today": today_val,
                "error": None,
            }
    return results

# ── Routes ──────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return HTML_PAGE

@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "GET":
        cfg = load_config()
        return jsonify({
            "client_id": cfg.get("google_client_id", ""),
            "has_secret": bool(cfg.get("google_client_secret", "")),
            "redirect_uri": cfg.get("redirect_uri", ""),
            "host": cfg.get("host", "0.0.0.0"),
            "port": cfg.get("port", 8080),
        })
    else:
        data = request.get_json()
        cfg = load_config()
        if "google_client_id" in data:
            cfg["google_client_id"] = data["google_client_id"]
        if "google_client_secret" in data and data["google_client_secret"]:
            cfg["google_client_secret"] = data["google_client_secret"]
        save_config(cfg)
        return jsonify({"status": "ok"})

@app.route("/api/auth/url")
def api_auth_url():
    cfg = load_config()
    if not cfg.get("google_client_id") or not cfg.get("google_client_secret"):
        return jsonify({"error": "Configure Client ID and Secret first"}), 400
    url, verifier, state = get_auth_url(cfg)
    tokens = load_tokens()
    tokens["pkce_verifier"] = verifier
    tokens["oauth_state"] = state
    save_tokens(tokens)
    return jsonify({"url": url})

@app.route("/oauth/callback")
def oauth_callback():
    cfg = load_config()
    tokens = load_tokens()
    code = request.args.get("code")
    state = request.args.get("state")
    error = request.args.get("error")

    if error:
        return redirect(f"/?error={error}")

    if state != tokens.get("oauth_state"):
        return redirect("/?error=state_mismatch")

    verifier = tokens.pop("pkce_verifier", None)
    tokens.pop("oauth_state", None)
    save_tokens(tokens)

    result = exchange_code(cfg, code, verifier)
    if "access_token" in result:
        tokens["access_token"] = result["access_token"]
        tokens["refresh_token"] = result.get("refresh_token", "")
        tokens["expires_at"] = time.time() + int(result.get("expires_in", 3600))
        save_tokens(tokens)
        return redirect("/?auth=success")
    else:
        return redirect(f"/?error=auth_failed&detail={result.get('error', 'unknown')}")

@app.route("/api/auth/status")
def api_auth_status():
    tokens = load_tokens()
    has_token = bool(tokens.get("access_token"))
    has_refresh = bool(tokens.get("refresh_token"))
    expires_at = tokens.get("expires_at", 0)
    is_valid = has_token and time.time() < expires_at
    return jsonify({
        "authenticated": has_token,
        "valid": is_valid,
        "has_refresh": has_refresh,
        "expires_at": expires_at,
    })

@app.route("/api/auth/revoke", methods=["POST"])
def api_auth_revoke():
    tokens = load_tokens()
    access_token = tokens.get("access_token")
    if access_token:
        requests.post(GOOGLE_REVOKE_URL, params={"token": access_token}, timeout=10)
    if os.path.exists(TOKENS_PATH):
        os.remove(TOKENS_PATH)
    return jsonify({"status": "ok"})

@app.route("/api/data/all")
def api_data_all():
    cfg = load_config()
    access_token, error = get_valid_token(cfg)
    if error:
        return jsonify({"error": error}), 401

    days = request.args.get("days", 30, type=int)
    results = fetch_all_data(access_token, days)
    return jsonify(results)

@app.route("/api/data/<data_type>")
def api_data_type(data_type):
    cfg = load_config()
    access_token, error = get_valid_token(cfg)
    if error:
        return jsonify({"error": error}), 401

    days = request.args.get("days", 30, type=int)
    raw = fetch_daily_rollup(access_token, data_type, days)
    return jsonify(raw)

# ── HTML UI ─────────────────────────────────────────────────────────────────
HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Fitbit Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
:root {
  --bg: #0f1117;
  --surface: #1a1d27;
  --surface2: #242836;
  --border: #2d3140;
  --text: #e1e4ed;
  --text2: #8b8fa3;
  --accent: #6c8cff;
  --accent2: #4ade80;
  --danger: #f87171;
  --warn: #fbbf24;
  --radius: 12px;
  --shadow: 0 2px 8px rgba(0,0,0,0.3);
}
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); min-height:100vh; }
button { cursor:pointer; font-family:inherit; }

/* Layout */
.header { background: var(--surface); border-bottom:1px solid var(--border); padding:16px 24px; display:flex; align-items:center; justify-content:space-between; }
.header h1 { font-size:1.25rem; font-weight:600; display:flex; align-items:center; gap:8px; }
.header-actions { display:flex; gap:8px; }

.btn { padding:8px 16px; border-radius:8px; border:none; font-size:0.875rem; font-weight:500; transition:background 0.2s; }
.btn-accent { background:var(--accent); color:#fff; }
.btn-accent:hover { background:#5a7ae6; }
.btn-ghost { background:transparent; color:var(--text2); border:1px solid var(--border); }
.btn-ghost:hover { background:var(--surface2); color:var(--text); }
.btn-danger { background:var(--danger); color:#fff; }
.btn-danger:hover { background:#e05b5b; }
.btn-sm { padding:6px 12px; font-size:0.8rem; }

.content { max-width:1280px; margin:0 auto; padding:24px; }

/* Metric Cards Grid */
.metrics-grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(220px, 1fr)); gap:16px; margin-bottom:32px; }
.metric-card { background:var(--surface); border:1px solid var(--border); border-radius:var(--radius); padding:20px; }
.metric-card .icon { font-size:1.5rem; margin-bottom:8px; }
.metric-card .name { font-size:0.8rem; color:var(--text2); text-transform:uppercase; letter-spacing:0.5px; margin-bottom:4px; }
.metric-card .value { font-size:1.75rem; font-weight:700; }
.metric-card .unit { font-size:0.85rem; color:var(--text2); }
.metric-card .nodata { font-size:0.85rem; color:var(--text2); font-style:italic; }

/* Charts Section */
.section-title { font-size:1rem; font-weight:600; margin-bottom:16px; color:var(--text2); }
.charts-grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(500px, 1fr)); gap:16px; }
.chart-card { background:var(--surface); border:1px solid var(--border); border-radius:var(--radius); padding:20px; }
.chart-card canvas { width:100% !important; height:250px !important; }

/* Settings Panel */
.overlay { display:none; position:fixed; inset:0; background:rgba(0,0,0,0.6); z-index:100; justify-content:center; align-items:center; }
.overlay.active { display:flex; }
.settings-panel { background:var(--surface); border:1px solid var(--border); border-radius:var(--radius); padding:32px; width:90%; max-width:480px; max-height:90vh; overflow-y:auto; }
.settings-panel h2 { font-size:1.1rem; margin-bottom:24px; }
.form-group { margin-bottom:16px; }
.form-group label { display:block; font-size:0.8rem; color:var(--text2); margin-bottom:4px; text-transform:uppercase; letter-spacing:0.5px; }
.form-group input { width:100%; padding:10px 12px; background:var(--bg); border:1px solid var(--border); border-radius:8px; color:var(--text); font-size:0.9rem; font-family:monospace; }
.form-group input:focus { outline:none; border-color:var(--accent); }
.form-group .hint { font-size:0.75rem; color:var(--text2); margin-top:4px; }

/* Auth Status */
.auth-status { display:flex; align-items:center; gap:8px; padding:12px 16px; border-radius:8px; margin-bottom:16px; font-size:0.85rem; }
.auth-status.connected { background:#0f2d1a; border:1px solid #1a5c2e; color:var(--accent2); }
.auth-status.disconnected { background:#2d1a0f; border:1px solid #5c2e1a; color:var(--warn); }
.auth-status .dot { width:8px; height:8px; border-radius:50%; }
.auth-status.connected .dot { background:var(--accent2); }
.auth-status.disconnected .dot { background:var(--warn); }

/* State */
.loading { text-align:center; padding:48px; color:var(--text2); }
.empty-state { text-align:center; padding:64px 24px; }
.empty-state .icon { font-size:3rem; margin-bottom:16px; }
.empty-state h2 { font-size:1.25rem; margin-bottom:8px; }
.empty-state p { color:var(--text2); margin-bottom:24px; max-width:400px; margin-left:auto; margin-right:auto; }

/* Debug panel */
#debugLog { position:fixed; bottom:0; left:0; right:0; z-index:999; background:#0d0d0dee; color:#0f0; font-family:monospace; font-size:0.7rem; max-height:120px; overflow-y:auto; padding:6px 12px; border-top:1px solid #333; display:none; }

/* Responsive */
@media (max-width: 600px) {
  .metrics-grid { grid-template-columns:repeat(auto-fill, minmax(150px, 1fr)); }
  .charts-grid { grid-template-columns:1fr; }
  .header { padding:12px 16px; }
  .content { padding:16px; }
}

.toast { position:fixed; top:16px; right:16px; z-index:200; padding:12px 20px; border-radius:8px; font-size:0.85rem; opacity:0; transform:translateY(-10px); transition:all 0.3s; pointer-events:none; }
.toast.show { opacity:1; transform:translateY(0); }
.toast.success { background:var(--accent2); color:#000; }
.toast.error { background:var(--danger); color:#fff; }
</style>
</head>
<body>

<div class="header">
  <h1>⌚ Fitbit Dashboard</h1>
  <div class="header-actions">
    <button class="btn btn-ghost btn-sm" onclick="refreshData()">🔄 Refresh</button>
    <button class="btn btn-ghost btn-sm" onclick="toggleSettings()">⚙ Settings</button>
  </div>
</div>

<div id="authBanner"></div>

<div class="content" id="content">
  <div class="loading">Loading your health data...</div>
</div>

<!-- Settings Overlay -->
<div class="overlay" id="settingsOverlay">
  <div class="settings-panel">
    <h2>⚙ Settings</h2>
    <div class="form-group">
      <label>Google Client ID</label>
      <input type="text" id="settingsClientId" placeholder="123456789-xxxxx.apps.googleusercontent.com">
    </div>
    <div class="form-group">
      <label>Google Client Secret</label>
      <input type="password" id="settingsClientSecret" placeholder="Enter new secret (leave blank to keep current)">
      <div class="hint">From Google Cloud Console → APIs & Services → Credentials</div>
    </div>
    <div class="form-group">
      <label>Redirect URI</label>
      <input type="text" id="settingsRedirectUri" placeholder="http://localhost:8080/oauth/callback">
      <div class="hint">Must match the authorized redirect URI in Google Cloud Console</div>
    </div>
    <div style="display:flex; gap:8px; margin-top:24px;">
      <button class="btn btn-accent" onclick="saveSettings()">💾 Save</button>
      <button class="btn btn-ghost" onclick="toggleSettings()">Cancel</button>
    </div>
    <hr style="border-color:var(--border); margin:24px 0;">
    <div style="display:flex; gap:8px;">
      <button class="btn btn-accent" id="signInBtn" onclick="signIn()">🔑 Sign in with Google</button>
      <button class="btn btn-danger btn-sm" id="revokeBtn" onclick="revokeAuth()" style="display:none;">Disconnect</button>
    </div>
  </div>
</div>

<!-- Debug panel -->
<div id="debugLog"></div>

<div class="toast" id="toast"></div>

<script>
// ── Debug panel ──────────────────────────────────────────────────────────
window._debugLog = [];
const MAX_DEBUG = 50;
function debug(msg) {
    window._debugLog.push(new Date().toISOString().slice(11,23) + ' ' + msg);
    if (window._debugLog.length > MAX_DEBUG) window._debugLog.shift();
    const el = document.getElementById('debugLog');
    if (el) {
        el.style.display = 'block';
        el.textContent = window._debugLog.join('\\n');
        el.scrollTop = el.scrollHeight;
    }
}
const _origLog = console.log;
console.log = function(...args) { _origLog.apply(console, args); debug(args.join(' ')); };
console.error = function(...args) { _origLog.apply(console, args); debug('ERROR: ' + args.join(' ')); };

// ── Toast ─────────────────────────────────────────────────────────────────
function showToast(msg, type) {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.className = 'toast ' + type + ' show';
    setTimeout(function() { t.classList.remove('show'); }, 3000);
}

// ── Settings ──────────────────────────────────────────────────────────────
let _settingsLoaded = false;

async function toggleSettings() {
    const overlay = document.getElementById('settingsOverlay');
    if (overlay.classList.contains('active')) {
        overlay.classList.remove('active');
    } else {
        overlay.classList.add('active');
        if (!_settingsLoaded) {
            await loadSettings();
            _settingsLoaded = true;
        }
        await checkAuthStatus();
    }
}

async function loadSettings() {
    try {
        const r = await fetch('/api/config');
        const cfg = await r.json();
        document.getElementById('settingsClientId').value = cfg.client_id || '';
        document.getElementById('settingsRedirectUri').value = cfg.redirect_uri || 'http://localhost:8080/oauth/callback';
    } catch(e) {
        debug('loadSettings error: ' + e);
    }
}

async function saveSettings() {
    const body = {
        google_client_id: document.getElementById('settingsClientId').value.trim(),
        google_client_secret: document.getElementById('settingsClientSecret').value,
    };
    try {
        const r = await fetch('/api/config', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body)
        });
        if (r.ok) {
            showToast('Settings saved', 'success');
            document.getElementById('settingsClientSecret').value = '';
        } else {
            showToast('Failed to save', 'error');
        }
    } catch(e) {
        showToast('Network error', 'error');
    }
}

// ── Auth ──────────────────────────────────────────────────────────────────
async function checkAuthStatus() {
    try {
        const r = await fetch('/api/auth/status');
        const s = await r.json();
        const signIn = document.getElementById('signInBtn');
        const revoke = document.getElementById('revokeBtn');
        if (s.authenticated) {
            signIn.textContent = s.valid ? '✅ Connected' : '⚠ Token Expired';
            revoke.style.display = '';
        } else {
            signIn.textContent = '🔑 Sign in with Google';
            revoke.style.display = 'none';
        }
        return s;
    } catch(e) { return {authenticated:false, valid:false}; }
}

async function signIn() {
    try {
        const r = await fetch('/api/auth/url');
        const data = await r.json();
        if (data.error) {
            showToast(data.error, 'error');
            return;
        }
        window.location.href = data.url;
    } catch(e) {
        showToast('Failed to get auth URL', 'error');
    }
}

async function revokeAuth() {
    if (!confirm('Disconnect your Google account? You will need to sign in again.')) return;
    try {
        await fetch('/api/auth/revoke', {method:'POST'});
        document.getElementById('signInBtn').textContent = '🔑 Sign in with Google';
        document.getElementById('revokeBtn').style.display = 'none';
        showToast('Disconnected', 'success');
        renderContent();
    } catch(e) {
        showToast('Failed to revoke', 'error');
    }
}

// ── Data Loading & Rendering ──────────────────────────────────────────────
let chartInstances = {};

function destroyCharts() {
    Object.values(chartInstances).forEach(function(c) { c.destroy(); });
    chartInstances = {};
}

function colorForIndex(i) {
    const colors = ['#6c8cff','#4ade80','#fbbf24','#f87171','#c084fc','#38bdf8','#fb923c','#a78bfa','#34d399','#f472b6'];
    return colors[i % colors.length];
}

function renderMetricCards(data) {
    let html = '<div class="metrics-grid">';
    let i = 0;
    for (const [key, info] of Object.entries(data)) {
        i++;
        if (info.error) continue;
        const val = info.today !== null && info.today !== undefined ? info.today : null;
        html += '<div class="metric-card">';
        html += '<div class="icon">' + (info.icon || '📊') + '</div>';
        html += '<div class="name">' + (info.name || key) + '</div>';
        if (val !== null) {
            html += '<div class="value">' + val + '</div>';
            html += '<div class="unit">' + (info.unit || '') + '</div>';
        } else {
            html += '<div class="nodata">No data yet</div>';
        }
        html += '</div>';
    }
    html += '</div>';
    return html;
}

function renderCharts(data) {
    let html = '<div class="section-title">📈 Trends (30 days)</div><div class="charts-grid">';
    let i = 0;
    for (const [key, info] of Object.entries(data)) {
        i++;
        if (info.error || !info.points || info.points.length < 2) continue;
        const chartId = 'chart-' + key.replace(/[^a-zA-Z0-9]/g, '_');
        html += '<div class="chart-card">';
        html += '<div style="font-size:0.85rem; color:var(--text2); margin-bottom:8px;">' + (info.icon||'') + ' ' + (info.name||key) + ' <span style="color:var(--text)">' + (info.today||'') + ' ' + (info.unit||'') + '</span></div>';
        html += '<canvas id="' + chartId + '"></canvas>';
        html += '</div>';
    }
    html += '</div>';
    return html;
}

function createChart(canvasId, points, label, color) {
    const ctx = document.getElementById(canvasId).getContext('2d');
    const dates = points.map(function(p) { return p.date; });
    const values = points.map(function(p) { return p.value; });

    chartInstances[canvasId] = new Chart(ctx, {
        type: 'line',
        data: {
            labels: dates,
            datasets: [{
                label: label,
                data: values,
                borderColor: color,
                backgroundColor: color + '20',
                fill: true,
                tension: 0.3,
                pointRadius: 1,
                borderWidth: 2,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
                x: {
                    ticks: { color: '#8b8fa3', font: {size:10}, maxTicksLimit: 8 },
                    grid: { color: '#2d3140' }
                },
                y: {
                    ticks: { color: '#8b8fa3', font: {size:10} },
                    grid: { color: '#2d3140' },
                    beginAtZero: false,
                }
            },
            interaction: { intersect: false, mode: 'index' },
        }
    });
}

async function refreshData() {
    const content = document.getElementById('content');
    content.innerHTML = '<div class="loading">Loading your health data...</div>';
    await loadAndRender();
}

async function loadAndRender() {
    const content = document.getElementById('content');
    const authBanner = document.getElementById('authBanner');
    const authStatus = await checkAuthStatus();

    // Auth banner
    if (!authStatus.authenticated) {
        authBanner.innerHTML = '<div class="auth-status disconnected" style="margin:16px 24px 0 24px;"><div class="dot"></div> Not connected — open ⚙ Settings to sign in with Google</div>';
        content.innerHTML = '<div class="empty-state"><div class="icon">⌚</div><h2>Connect Your Fitbit</h2><p>Sign in with your Google account to pull in all your Fitbit health data — steps, heart rate, sleep, and more.</p><button class="btn btn-accent" onclick="toggleSettings()">⚙ Open Settings</button></div>';
        return;
    }

    if (!authStatus.valid) {
        authBanner.innerHTML = '<div class="auth-status disconnected" style="margin:16px 24px 0 24px;"><div class="dot"></div> Token expired — <a href="#" onclick="signIn();return false;" style="color:var(--accent)">reconnect</a></div>';
        content.innerHTML = '<div class="empty-state"><div class="icon">⚠</div><h2>Session Expired</h2><p>Your Google connection needs to be refreshed.</p><button class="btn btn-accent" onclick="signIn()">🔑 Sign in Again</button></div>';
        return;
    }

    authBanner.innerHTML = '';

    try {
        const r = await fetch('/api/data/all');
        if (r.status === 401) {
            content.innerHTML = '<div class="empty-state"><div class="icon">🔒</div><h2>Authentication Required</h2><p>Your session has expired. Please sign in again.</p><button class="btn btn-accent" onclick="signIn()">🔑 Sign in with Google</button></div>';
            return;
        }
        const data = await r.json();
        debug('Loaded data types: ' + Object.keys(data).length);

        destroyCharts();
        let html = renderMetricCards(data);
        html += renderCharts(data);

        // Add sleep section if no sleep data type exists (handle separately)
        content.innerHTML = html || '<div class="empty-state"><div class="icon">📭</div><h2>No Data Yet</h2><p>Once your Fitbit syncs data, metrics and charts will appear here.</p></div>';

        // Initialize charts
        let i = 0;
        for (const [key, info] of Object.entries(data)) {
            if (info.error || !info.points || info.points.length < 2) continue;
            i++;
            const chartId = 'chart-' + key.replace(/[^a-zA-Z0-9]/g, '_');
            const canvas = document.getElementById(chartId);
            if (canvas) {
                createChart(chartId, info.points, info.name, colorForIndex(i));
            }
        }
    } catch(e) {
        content.innerHTML = '<div class="empty-state"><div class="icon">⚠</div><h2>Connection Error</h2><p>Could not load data: ' + e.message + '</p><button class="btn btn-accent" onclick="refreshData()">🔄 Retry</button></div>';
        debug('loadAndRender error: ' + e);
    }
}

// ── Init ──────────────────────────────────────────────────────────────────
(function init() {
    // Check for auth callback result
    const params = new URLSearchParams(window.location.search);
    if (params.get('auth') === 'success') {
        showToast('Connected! Loading your data...', 'success');
        window.history.replaceState({}, '', '/');
    } else if (params.get('error')) {
        showToast('Authentication failed: ' + params.get('error'), 'error');
        window.history.replaceState({}, '', '/');
    }

    loadAndRender();
})();
</script>
</body>
</html>"""

# ── Main ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    cfg = load_config()
    host = cfg.get("host", "0.0.0.0")
    port = cfg.get("port", 8080)

    local_ip = socket.gethostbyname(socket.gethostname())
    print(f"Fitbit Dashboard running at:")
    print(f"  Local:   http://localhost:{port}")
    print(f"  Network: http://{local_ip}:{port}")
    print(f"  Redirect URI must match: {cfg.get('redirect_uri', 'http://localhost:8080/oauth/callback')}")

    app.run(host=host, port=port, debug=False, ssl_context=("cert.pem", "cert.key"))
