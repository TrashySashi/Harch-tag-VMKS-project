"""
HArch Tag — scoring server.

A small, self-contained Flask app that counts IR hits reported by the vest
(ESP32-WROOM-32) over Wi-Fi. Deliberately has no camera/picamera2 dependency,
so it runs unchanged on a laptop for development and on the Pi 5 for the game.

Endpoints
    GET  /         live scoreboard page (auto-updates via SSE)
    POST /hit      vest reports a hit; increments the counter
    POST /reset    start a fresh round (counter back to 0)
    GET  /score    current count as JSON: {"hits": N}
    GET  /events   Server-Sent Events stream pushing the count on every change

State is a single in-memory integer; it resets when the process restarts.

Run:  python score_server.py   (listens on 0.0.0.0:5001)
"""

from flask import Flask, Response, request, jsonify, render_template_string
import threading
import queue
import json
import time
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("harchtag.score")

app = Flask(__name__)


def _client() -> str:
    """Best-effort client identifier for logs (IP, honoring proxies)."""
    fwd = request.headers.get("X-Forwarded-For", "")
    return fwd.split(",")[0].strip() if fwd else (request.remote_addr or "?")

# --- game state ----------------------------------------------------------
_lock = threading.Lock()
_hits = 0

# Each connected scoreboard browser registers a queue here; on every state
# change we push the new count to all of them (Server-Sent Events).
_subscribers: "set[queue.Queue]" = set()
_subscribers_lock = threading.Lock()

# Debounce: ignore hit reports that arrive within this window of the last one,
# so a single shot (which the TSOP may see as a burst) counts only once.
_DEBOUNCE_S = 0.3
_last_hit_ts = 0.0


def _broadcast(hits: int) -> None:
    """Push the current count to every connected scoreboard."""
    with _subscribers_lock:
        for q in _subscribers:
            q.put(hits)


@app.route("/hit", methods=["POST"])
def hit():
    """Vest reports a validated IR hit."""
    global _hits, _last_hit_ts

    now = time.monotonic()
    with _lock:
        if now - _last_hit_ts < _DEBOUNCE_S:
            since = now - _last_hit_ts
            log.warning("HIT ignored (debounced %.0fms < %.0fms) from %s",
                        since * 1000, _DEBOUNCE_S * 1000, _client())
            return jsonify(hits=_hits, counted=False)
        _last_hit_ts = now
        _hits += 1
        current = _hits

    log.info("HIT from %s  ->  total hits = %d", _client(), current)
    _broadcast(current)
    return jsonify(hits=current, counted=True)


@app.route("/reset", methods=["POST"])
def reset():
    """Start a fresh round."""
    global _hits
    with _lock:
        _hits = 0
        current = _hits
    log.info("RESET by %s  ->  new round, hits = 0", _client())
    _broadcast(current)
    return jsonify(hits=current)


@app.route("/score")
def score():
    with _lock:
        current = _hits
    log.debug("SCORE queried by %s  ->  %d", _client(), current)
    return jsonify(hits=current)


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
            # Send the current value immediately so a fresh page is correct.
            with _lock:
                yield f"data: {json.dumps({'hits': _hits})}\n\n"
            while True:
                hits = q.get()
                yield f"data: {json.dumps({'hits': hits})}\n\n"
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
    .count { font-size:9rem; font-weight:bold; line-height:1;
             color:#ff4040; text-shadow:0 0 20px rgba(255,64,64,.5); }
    .label { font-size:.9rem; color:#666; }
    button { font-family:monospace; font-size:.9rem; padding:10px 24px;
             background:#222; color:#eee; border:1px solid #444;
             border-radius:4px; cursor:pointer; }
    button:hover { background:#333; }
  </style>
</head>
<body>
  <h1>HARCH TAG</h1>
  <div class="count" id="count">0</div>
  <div class="label">HITS TAKEN</div>
  <button onclick="resetScore()">RESET ROUND</button>
  <script>
    const el = document.getElementById("count");
    const es = new EventSource("/events");
    es.onmessage = (e) => { el.textContent = JSON.parse(e.data).hits; };
    function resetScore() { fetch("/reset", { method: "POST" }); }
  </script>
</body>
</html>"""


@app.route("/")
def index():
    return render_template_string(_PAGE)


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
    log.info("Endpoints: POST /hit  POST /reset  GET /score  GET /events  GET /")
    try:
        app.run(host="0.0.0.0", port=PORT, threaded=True)
    except Exception:
        log.exception("Server failed to start")
        raise
    finally:
        log.info("Score server stopped")
