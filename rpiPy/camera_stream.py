from flask import Flask, Response, render_template_string, jsonify
from picamera2 import Picamera2
import cv2
import numpy as np
import threading
import time
import ups_monitor

app = Flask(__name__)
ups_monitor.start()

picam2 = Picamera2(0)
config = picam2.create_video_configuration(
    main={"format": "RGB888", "size": (1280, 720)},
    controls={"FrameRate": 30},
)
picam2.configure(config)
picam2.start()

_frame_lock = threading.Lock()
_latest_frame = None

# Red wraps around both ends of the HSV hue wheel (0-10 and 170-180)
_RED_LOWER1 = np.array([0,   110,  60])
_RED_UPPER1 = np.array([10,  255, 255])
_RED_LOWER2 = np.array([170, 110,  60])
_RED_UPPER2 = np.array([180, 255, 255])

# Centre region where no arrow is shown (fraction of half-frame)
_DEADZONE_X = 0.15
_DEADZONE_Y = 0.15
_MIN_AREA   = 3000   # px² — ignore small noise blobs

_ARROW_COLOR   = (0, 165, 255)   # orange
_CONTOUR_COLOR = (0, 255,   0)   # green
_CENTROID_COLOR= (0, 255, 255)   # yellow


def _draw_arrow(frame, direction, h, w):
    cx, cy = w // 2, h // 2
    thick, tip = 5, 0.45

    if direction == "LEFT":
        cv2.arrowedLine(frame, (110, cy), (30, cy), _ARROW_COLOR, thick, tipLength=tip)
        cv2.putText(frame, "ROTATE LEFT",  (120, cy + 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, _ARROW_COLOR, 2)

    elif direction == "RIGHT":
        cv2.arrowedLine(frame, (w - 110, cy), (w - 30, cy), _ARROW_COLOR, thick, tipLength=tip)
        cv2.putText(frame, "ROTATE RIGHT", (w - 310, cy + 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, _ARROW_COLOR, 2)

    elif direction == "UP":
        cv2.arrowedLine(frame, (cx, 110), (cx, 30), _ARROW_COLOR, thick, tipLength=tip)
        cv2.putText(frame, "TILT UP",   (cx - 55, 130),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, _ARROW_COLOR, 2)

    elif direction == "DOWN":
        cv2.arrowedLine(frame, (cx, h - 110), (cx, h - 30), _ARROW_COLOR, thick, tipLength=tip)
        cv2.putText(frame, "TILT DOWN", (cx - 65, h - 120),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, _ARROW_COLOR, 2)


def _process(frame):
    h, w = frame.shape[:2]
    cx, cy = w // 2, h // 2
    dz_x   = int(w * _DEADZONE_X)
    dz_y   = int(h * _DEADZONE_Y)

    # --- red mask --------------------------------------------------------
    hsv   = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask  = cv2.bitwise_or(
        cv2.inRange(hsv, _RED_LOWER1, _RED_UPPER1),
        cv2.inRange(hsv, _RED_LOWER2, _RED_UPPER2),
    )
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    mask   = cv2.morphologyEx(mask, cv2.MORPH_OPEN,   kernel)
    mask   = cv2.morphologyEx(mask, cv2.MORPH_DILATE, kernel)

    # --- overlay: centre crosshair + deadzone box ------------------------
    cv2.drawMarker(frame, (cx, cy), (255, 255, 255), cv2.MARKER_CROSS, 40, 1)
    cv2.rectangle(frame,
                  (cx - dz_x, cy - dz_y),
                  (cx + dz_x, cy + dz_y),
                  (180, 180, 180), 1)

    # --- find shirt contour ----------------------------------------------
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    if not contours:
        cv2.putText(frame, "NO RED TARGET", (cx - 130, 50),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 255), 2)
        return frame

    largest = max(contours, key=cv2.contourArea)
    if cv2.contourArea(largest) < _MIN_AREA:
        cv2.putText(frame, "NO RED TARGET", (cx - 130, 50),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 255), 2)
        return frame

    # Draw shirt outline
    cv2.drawContours(frame, [largest], -1, _CONTOUR_COLOR, 2)

    # Bounding box (subtle, for size reference)
    x, y, bw, bh = cv2.boundingRect(largest)
    cv2.rectangle(frame, (x, y), (x + bw, y + bh), (0, 200, 0), 1)

    # Centroid
    M = cv2.moments(largest)
    if M["m00"] == 0:
        return frame
    obj_x = int(M["m10"] / M["m00"])
    obj_y = int(M["m01"] / M["m00"])

    cv2.circle(frame, (obj_x, obj_y), 7, _CENTROID_COLOR, -1)
    cv2.line(frame, (cx, cy), (obj_x, obj_y), _CENTROID_COLOR, 1)

    # Offset as % of half-frame
    off_x = obj_x - cx
    off_y = obj_y - cy
    pct_x = off_x / (w / 2) * 100
    pct_y = off_y / (h / 2) * 100

    cv2.putText(frame, f"offset  X: {pct_x:+.0f}%   Y: {pct_y:+.0f}%",
                (10, h - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (200, 200, 200), 1)

    # Directional arrows / centred label
    in_x = abs(off_x) <= dz_x
    in_y = abs(off_y) <= dz_y

    if in_x and in_y:
        cv2.putText(frame, "CENTERED", (cx - 75, 50),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 255, 0), 2)
    else:
        if not in_x:
            _draw_arrow(frame, "LEFT"  if off_x < 0 else "RIGHT", h, w)
        if not in_y:
            _draw_arrow(frame, "UP"    if off_y < 0 else "DOWN",  h, w)

    return frame


def _capture_loop():
    global _latest_frame
    while True:
        raw       = picam2.capture_array()
        processed = _process(raw)
        with _frame_lock:
            _latest_frame = processed


def _mjpeg_generator():
    while True:
        with _frame_lock:
            frame = _latest_frame
        if frame is None:
            time.sleep(0.01)
            continue
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not ok:
            continue
        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n" + buf.tobytes() + b"\r\n"
        )
        time.sleep(1 / 30)


_PAGE = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Red Shirt Tracker</title>
  <style>
    body { margin:0; background:#111; display:flex; flex-direction:column;
           align-items:center; justify-content:center; min-height:100vh; gap:10px; }
    h1   { color:#eee; font-family:monospace; font-size:.95rem; margin:0; }
    img  { max-width:100%; border:2px solid #333; border-radius:4px; }
    p    { color:#666; font-family:monospace; font-size:.75rem; margin:0; }
    a    { color:#4af; font-family:monospace; font-size:.85rem; }
  </style>
</head>
<body>
  <h1>Red Shirt Tracker &mdash; RPi Camera Module 3 &mdash; MIPI 0</h1>
  <img src="/video_feed" alt="camera stream">
  <p>Green outline = detected shirt &nbsp;|&nbsp;
     Orange arrows = direction to rotate camera &nbsp;|&nbsp;
     CENTERED = shirt in deadzone</p>
  <a href="/ups">UPS Monitor &rarr;</a>
</body>
</html>"""

_UPS_PAGE = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>UPS Monitor</title>
  <style>
    *    { box-sizing:border-box; }
    body { margin:0; background:#111; color:#eee; font-family:monospace;
           display:flex; flex-direction:column; align-items:center;
           justify-content:center; min-height:100vh; gap:24px; }
    h1   { font-size:1rem; margin:0; }
    .cards { display:flex; flex-wrap:wrap; gap:16px; justify-content:center; }
    .card  { background:#1e1e1e; border:1px solid #333; border-radius:8px;
             padding:18px 28px; text-align:center; min-width:150px; }
    .card .val  { font-size:2rem; font-weight:bold; margin:4px 0; }
    .card .lbl  { font-size:.75rem; color:#888; }
    .bar-wrap   { width:320px; background:#333; border-radius:6px; height:22px; overflow:hidden; }
    .bar        { height:100%; border-radius:6px; transition:width .5s; }
    .status     { font-size:1.1rem; padding:6px 18px; border-radius:20px; }
    .charging   { background:#1a4a1a; color:#4f4; border:1px solid #4f4; }
    .discharging{ background:#4a1a1a; color:#f44; border:1px solid #f44; }
    .unavail    { color:#666; font-size:.9rem; }
    a           { color:#4af; font-size:.85rem; }
  </style>
</head>
<body>
  <h1>Waveshare UPS Module 3S</h1>
  <div id="unavail" class="unavail" style="display:none">
    UPS not detected — check wiring or run: sudo raspi-config → Interface → I2C
  </div>
  <div id="content">
    <div class="cards">
      <div class="card">
        <div class="lbl">Battery</div>
        <div class="val" id="pct">--%</div>
        <div class="bar-wrap"><div class="bar" id="bar" style="width:0%;background:#4f4"></div></div>
      </div>
      <div class="card">
        <div class="lbl">Voltage</div>
        <div class="val" id="volt">-- V</div>
        <div class="lbl">3S Li-ion (9–12.6 V)</div>
      </div>
      <div class="card">
        <div class="lbl">Current</div>
        <div class="val" id="curr">-- mA</div>
        <div class="lbl">negative = charging</div>
      </div>
      <div class="card">
        <div class="lbl">Power</div>
        <div class="val" id="pwr">-- W</div>
        <div class="lbl">load consumption</div>
      </div>
    </div>
    <div id="status" class="status discharging">--</div>
  </div>
  <a href="/">&#8592; Camera</a>
  <script>
    async function refresh() {
      try {
        const d = await fetch('/ups/data').then(r => r.json());
        if (!d.available) {
          document.getElementById('unavail').style.display = '';
          document.getElementById('content').style.display = 'none';
          return;
        }
        document.getElementById('unavail').style.display = 'none';
        document.getElementById('content').style.display = '';
        const p = d.percent;
        document.getElementById('pct').textContent   = p.toFixed(1) + '%';
        document.getElementById('volt').textContent  = d.voltage_v.toFixed(3) + ' V';
        document.getElementById('curr').textContent  = d.current_ma.toFixed(0) + ' mA';
        document.getElementById('pwr').textContent   = d.power_w.toFixed(2) + ' W';
        const bar = document.getElementById('bar');
        bar.style.width = p + '%';
        bar.style.background = p > 50 ? '#4f4' : p > 20 ? '#fa4' : '#f44';
        const st = document.getElementById('status');
        st.textContent  = d.status;
        st.className    = 'status ' + (d.charging ? 'charging' : 'discharging');
      } catch(e) {}
    }
    refresh();
    setInterval(refresh, 2000);
  </script>
</body>
</html>"""


@app.route("/")
def index():
    return render_template_string(_PAGE)


@app.route("/video_feed")
def video_feed():
    return Response(
        _mjpeg_generator(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/ups")
def ups_page():
    return render_template_string(_UPS_PAGE)


@app.route("/ups/data")
def ups_data():
    data = ups_monitor.latest()
    if data is None:
        return jsonify({"available": False})
    data["available"] = True
    return jsonify(data)


if __name__ == "__main__":
    threading.Thread(target=_capture_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=5000, threaded=True)
