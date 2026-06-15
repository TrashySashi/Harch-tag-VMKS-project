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

app = Flask(__name__)

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
    import time

    now = time.monotonic()
    with _lock:
        if now - _last_hit_ts < _DEBOUNCE_S:
            return jsonify(hits=_hits, counted=False)
        _last_hit_ts = now
        _hits += 1
        current = _hits

    _broadcast(current)
    return jsonify(hits=current, counted=True)


@app.route("/reset", methods=["POST"])
def reset():
    """Start a fresh round."""
    global _hits
    with _lock:
        _hits = 0
        current = _hits
    _broadcast(current)
    return jsonify(hits=current)


@app.route("/score")
def score():
    with _lock:
        current = _hits
    return jsonify(hits=current)


@app.route("/events")
def events():
    """Server-Sent Events: stream the hit count to the scoreboard page."""
    def stream():
        q: queue.Queue = queue.Queue()
        with _subscribers_lock:
            _subscribers.add(q)
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


if __name__ == "__main__":
    # Port 5001 so it can run alongside the camera app (port 5000) on the Pi.
    app.run(host="0.0.0.0", port=5001, threaded=True)
