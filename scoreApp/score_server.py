"""
HArch Tag — scoring server.

A small, self-contained Flask app that counts IR hits reported by the vest
(ESP32-WROOM-32) over Wi-Fi. Deliberately has no camera/picamera2 dependency,
so it runs unchanged on a laptop for development and on the Pi 5 for the game.

The score has three figures, all reset at the start of every round:
    hits   — IR hits the vest registered (reported by the vest, POST /hit)
    shots  — shots the probe fired       (reported by the probe, POST /shot)
    misses — shots that didn't land = max(0, shots - hits)

Hits and shots come from two independent devices that don't talk to each
other, so `misses` is recomputed from both counters on every /hit and every
/shot — never stored on its own — so the two sources can't drift out of sync.

Endpoints
    GET  /         start screen + live scoreboard (auto-updates via SSE)
    POST /start    begin a game: activate scoring, reset counts, start the probe
    POST /stop     end the game: deactivate scoring, stop the probe
    POST /hit      vest reports a hit; counted only while a game is running
    POST /shot     probe reports a fired shot; counted only while running
    POST /reset    start a fresh round (counters back to 0, game stays active)
    GET  /score    current state as JSON: {"active": bool, "hits": N,
                                           "shots": N, "misses": N}
    GET  /events   Server-Sent Events stream pushing state on every change

State is in-memory; it resets when the process restarts.

Run:  python score_server.py   (listens on 0.0.0.0:5001)
"""

from flask import Flask, Response, request, jsonify, render_template_string
import threading
import queue
import json
import time
import logging
import os

import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("harchtag.score")

# Base URL of the probe / camera_stream server (port 5000). Configured via .env
# so the Pi's address isn't hard-coded. e.g. PROBE_URL=http://10.62.210.69:5000
PROBE_URL = os.getenv("PROBE_URL", "http://127.0.0.1:5000").rstrip("/")
PROBE_TIMEOUT_S = float(os.getenv("PROBE_TIMEOUT_S", "3"))

app = Flask(__name__)


def _client() -> str:
    """Best-effort client identifier for logs (IP, honoring proxies)."""
    fwd = request.headers.get("X-Forwarded-For", "")
    return fwd.split(",")[0].strip() if fwd else (request.remote_addr or "?")

# --- game state ----------------------------------------------------------
_lock = threading.Lock()
_hits = 0                # IR hits the vest registered
_shots = 0               # shots the probe reported firing
_active = False          # is a game currently running?

# Each connected scoreboard browser registers a queue here; on every state
# change we push the new state to all of them (Server-Sent Events).
_subscribers: "set[queue.Queue]" = set()
_subscribers_lock = threading.Lock()

# Debounce: ignore hit reports that arrive within this window of the last one,
# so a single shot (which the TSOP may see as a burst) counts only once.
_DEBOUNCE_S = 0.3
_last_hit_ts = 0.0
_last_shot_ts = 0.0


def _snapshot() -> dict:
    """Current game state as a plain dict. Call while holding _lock.

    `misses` is derived here (never stored) so it always reflects the latest
    hit *and* shot counts — recomputed on every /hit and /shot. Clamped at 0
    because the two counters come from independent devices and can momentarily
    arrive out of order (a hit logged just before its shot).
    """
    return {
        "active": _active,
        "hits": _hits,
        "shots": _shots,
        "misses": max(0, _shots - _hits),
    }


def _broadcast(state: dict) -> None:
    """Push the current game state to every connected scoreboard."""
    with _subscribers_lock:
        for q in _subscribers:
            q.put(state)


def _call_probe(action: str) -> None:
    """POST /start or /stop to the probe (camera_stream) server.

    Failures are logged but never raise — a probe that's offline (e.g. during
    laptop development) must not break the game flow on the score app.
    """
    url = f"{PROBE_URL}/{action}"
    try:
        resp = requests.post(url, timeout=PROBE_TIMEOUT_S)
        log.info("probe %s -> %s %d %s",
                 action, url, resp.status_code, resp.text.strip())
    except requests.RequestException as e:
        log.warning("probe %s failed (%s): %s", action, url, e)


def start_probe() -> None:
    """Tell the probe (camera/aiming on the Pi) to start hunting."""
    _call_probe("start")


def stop_probe() -> None:
    """Tell the probe to stop hunting and stand down."""
    _call_probe("stop")


@app.route("/start", methods=["POST"])
def start():
    """Begin a game: activate scoring, reset the counts, start the probe."""
    global _active, _hits, _shots, _last_hit_ts, _last_shot_ts
    with _lock:
        _active = True
        _hits = 0
        _shots = 0
        _last_hit_ts = 0.0
        _last_shot_ts = 0.0
        snap = _snapshot()

    log.info("GAME STARTED by %s", _client())
    start_probe()          # mock for now — kicks off the camera/aiming probe
    _broadcast(snap)
    return jsonify(snap)


@app.route("/stop", methods=["POST"])
def stop():
    """End the game: deactivate scoring, stop the probe, return to home."""
    global _active
    with _lock:
        _active = False
        snap = _snapshot()

    log.info("GAME STOPPED by %s  ->  final hits = %d", _client(), snap["hits"])
    stop_probe()           # mock for now — stands the probe down
    _broadcast(snap)
    return jsonify(snap)


@app.route("/hit", methods=["POST"])
def hit():
    """Vest reports a validated IR hit."""
    global _hits, _last_hit_ts

    now = time.monotonic()
    with _lock:
        if not _active:
            log.warning("HIT ignored (no game running) from %s", _client())
            return jsonify(_snapshot() | {"counted": False})
        if now - _last_hit_ts < _DEBOUNCE_S:
            since = now - _last_hit_ts
            log.warning("HIT ignored (debounced %.0fms < %.0fms) from %s",
                        since * 1000, _DEBOUNCE_S * 1000, _client())
            return jsonify(_snapshot() | {"counted": False})
        _last_hit_ts = now
        _hits += 1
        snap = _snapshot()

    log.info("HIT from %s  ->  hits = %d  shots = %d  misses = %d",
             _client(), snap["hits"], snap["shots"], snap["misses"])
    _broadcast(snap)
    return jsonify(snap | {"counted": True})


@app.route("/shot", methods=["POST"])
def shot():
    """Probe reports it fired a shot."""
    global _shots, _last_shot_ts

    now = time.monotonic()
    with _lock:
        if not _active:
            log.warning("SHOT ignored (no game running) from %s", _client())
            return jsonify(_snapshot() | {"counted": False})
        if now - _last_shot_ts < _DEBOUNCE_S:
            since = now - _last_shot_ts
            log.warning("SHOT ignored (debounced %.0fms < %.0fms) from %s",
                        since * 1000, _DEBOUNCE_S * 1000, _client())
            return jsonify(_snapshot() | {"counted": False})
        _last_shot_ts = now
        _shots += 1
        snap = _snapshot()

    log.info("SHOT from %s  ->  hits = %d  shots = %d  misses = %d",
             _client(), snap["hits"], snap["shots"], snap["misses"])
    _broadcast(snap)
    return jsonify(snap | {"counted": True})


@app.route("/reset", methods=["POST"])
def reset():
    """Start a fresh round (keeps the game active, zeroes the counts)."""
    global _hits, _shots, _last_hit_ts, _last_shot_ts
    with _lock:
        _hits = 0
        _shots = 0
        _last_hit_ts = 0.0
        _last_shot_ts = 0.0
        snap = _snapshot()
    log.info("RESET by %s  ->  new round, hits = 0  shots = 0", _client())
    _broadcast(snap)
    return jsonify(snap)


@app.route("/score")
def score():
    with _lock:
        snap = _snapshot()
    log.debug("SCORE queried by %s  ->  %s", _client(), snap)
    return jsonify(snap)


@app.route("/events")
def events():
    """Server-Sent Events: stream the hit count to the scoreboard page."""
    client = _client()

    def stream():
        q: queue.Queue = queue.Queue()
        with _subscribers_lock:
            _subscribers.add(q)
            count = len(_subscribers)
        log.info("Scoreboard connected: %s  (%d watching)", client, count)
        try:
            # Send the current state immediately so a fresh page is correct.
            with _lock:
                yield f"data: {json.dumps(_snapshot())}\n\n"
            while True:
                state = q.get()
                yield f"data: {json.dumps(state)}\n\n"
        finally:
            with _subscribers_lock:
                _subscribers.discard(q)
                count = len(_subscribers)
            log.info("Scoreboard disconnected: %s  (%d watching)", client, count)

    return Response(stream(), mimetype="text/event-stream")


_PAGE = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>HArch Tag &mdash; Score</title>
  <style>
    body { margin:0; background:#111; color:#eee; font-family:monospace;
           display:flex; flex-direction:column; align-items:center;
           justify-content:center; min-height:100vh; gap:24px; }
    h1     { font-size:1rem; color:#aaa; margin:0; letter-spacing:2px; }
    .stats { display:flex; gap:48px; align-items:flex-end; }
    .stat  { display:flex; flex-direction:column; align-items:center; gap:8px; }
    .count { font-size:9rem; font-weight:bold; line-height:1;
             color:#ff4040; text-shadow:0 0 20px rgba(255,64,64,.5); }
    .count.minor { font-size:4.5rem; color:#bbb; text-shadow:none; }
    .count.miss  { font-size:4.5rem; color:#ffb040;
                   text-shadow:0 0 14px rgba(255,176,64,.4); }
    .label { font-size:.9rem; color:#666; }
    .intro { font-size:.85rem; color:#888; max-width:24rem; text-align:center; }
    button { font-family:monospace; font-size:.9rem; padding:10px 24px;
             background:#222; color:#eee; border:1px solid #444;
             border-radius:4px; cursor:pointer; }
    button:hover { background:#333; }
    .start { font-size:1.2rem; padding:16px 40px; border-color:#ff4040;
             color:#ff6b6b; }
    .stop  { border-color:#ff4040; color:#ff6b6b; }
    .hidden { display:none !important; }
  </style>
</head>
<body>
  <h1>HARCH TAG</h1>

  <!-- Start screen: shown only while the probe is deactivated -->
  <div id="startScreen" class="{{ 'hidden' if active else '' }}">
    <p class="intro">Put on the vest, then start the game. The probe will begin
       hunting and every IR hit you take is counted.</p>
    <div style="text-align:center; margin-top:20px;">
      <button class="start" onclick="startGame()">START GAME</button>
    </div>
  </div>

  <!-- Scoreboard: shown only while the probe is activated -->
  <div id="scoreScreen" class="{{ '' if active else 'hidden' }}"
       style="display:flex; flex-direction:column; align-items:center; gap:24px;">
    <div class="stats">
      <div class="stat">
        <div class="count minor" id="shots">{{ shots }}</div>
        <div class="label">SHOTS FIRED</div>
      </div>
      <div class="stat">
        <div class="count" id="count">{{ hits }}</div>
        <div class="label">HITS TAKEN</div>
      </div>
      <div class="stat">
        <div class="count miss" id="misses">{{ misses }}</div>
        <div class="label">MISSES</div>
      </div>
    </div>
    <div style="display:flex; gap:12px;">
      <button onclick="resetScore()">RESET ROUND</button>
      <button class="stop" onclick="stopGame()">STOP GAME</button>
    </div>
  </div>

  <script>
    const startScreen = document.getElementById("startScreen");
    const scoreScreen = document.getElementById("scoreScreen");
    const count = document.getElementById("count");
    const shots = document.getElementById("shots");
    const misses = document.getElementById("misses");

    function render(state) {
      count.textContent = state.hits;
      shots.textContent = state.shots;
      misses.textContent = state.misses;
      startScreen.classList.toggle("hidden", state.active);
      scoreScreen.classList.toggle("hidden", !state.active);
    }

    const es = new EventSource("/events");
    es.onmessage = (e) => render(JSON.parse(e.data));

    function startGame()  { fetch("/start", { method: "POST" }); }
    function stopGame()   { fetch("/stop",  { method: "POST" }); }
    function resetScore() { fetch("/reset", { method: "POST" }); }
  </script>
</body>
</html>"""


@app.route("/")
def index():
    with _lock:
        snap = _snapshot()
    # Render the correct screen server-side so there's no flash of the wrong
    # one before the first SSE update arrives.
    return render_template_string(
        _PAGE, active=snap["active"], hits=snap["hits"],
        shots=snap["shots"], misses=snap["misses"])


@app.errorhandler(Exception)
def on_error(e):
    """Log any unhandled failure with a full traceback, return JSON 500.

    Normal HTTP errors (404, 405, ...) are passed through unchanged.
    """
    from werkzeug.exceptions import HTTPException

    if isinstance(e, HTTPException):
        log.warning("%s %s -> %d (%s)", request.method, request.path,
                    e.code, e.name)
        return e
    log.exception("Request to %s %s failed: %s", request.method, request.path, e)
    return jsonify(error=str(e)), 500


if __name__ == "__main__":
    # Port 5001 so it can run alongside the camera app (port 5000) on the Pi.
    PORT = 5001
    log.info("HArch Tag score server starting on http://0.0.0.0:%d", PORT)
    log.info("Endpoints: POST /start  POST /stop  POST /hit  POST /shot  POST /reset  GET /score  GET /events  GET /")
    try:
        app.run(host="0.0.0.0", port=PORT, threaded=True)
    except Exception:
        log.exception("Server failed to start")
        raise
    finally:
        log.info("Score server stopped")
