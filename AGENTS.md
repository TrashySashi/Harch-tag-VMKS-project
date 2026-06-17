# AGENTS.md — HArch Tag

Context for AI agents working on the HArch Tag project. Read this before generating
code, wiring, or documentation so suggestions match the actual hardware and design decisions.

**Documentation maintenance rule:** Whenever a change is made to the codebase — new files,
removed files, renamed endpoints, changed hardware roles, updated software architecture, or
any other structural modification — the relevant sections of this file (AGENTS.md) and any
other documentation files in the project must be updated to reflect the new state. Do not
leave documentation describing code or structure that no longer exists.

## Project overview

HArch Tag is a **single-player laser-tag game** inspired by the *Marksman-H training
remote* from Star Wars. An autonomous device hunts the player and fires infrared "laser"
shots; the player's goal is to dodge them. It is a school project for the course
"Вградени микрокомпютърни системи – Практика" (Embedded Microcomputer Systems – Practice)
at Технологично училище „Електронни системи" (Technical University – Sofia).

The system has **two physical components** that communicate wirelessly:

1. **The probe** — the autonomous shooter on a moving platform.
2. **The vest** — worn by the player, detects and counts IR hits.

### Core game loop
Camera sees player → Pi runs object detection → motion system aims at player →
gun fires modulated IR → vest's IR receivers register the hit → score updates.

## Important design decisions (read these first)

These supersede anything in the original Word documentation:

- **Normal camera, not IR camera.** Player is located by an object-detection algorithm
  on a standard RGB camera. (Originally the design used an IR camera + an IR beacon on
  the vest.)
- **No lightsaber.** The original third component (an IR-sensor toy for blocking shots)
  has been **dropped from scope**.
- **Vest IR beacon LEDs (VSMB1940X01) are removed.** They existed only to make the vest
  visible to an IR camera. With normal-camera object detection they are unnecessary.
- **Use pre-trained models, do not train from scratch.** Short deadline; the developer is
  new to embedded systems and to object detection.
- **Detection runs software-only on the Pi 5** — no AI accelerator (no Hailo/Coral).
- **Board roles (current):**
  - **Vest controller = ESP32-WROOM-32 DevKitC** (classic dual-core Xtensa; Wi-Fi +
    Bluetooth Classic + BLE).
  - **Probe = Raspberry Pi 5 directly** — the Pi handles vision, motion, aiming, and IR
    firing itself; there is **no separate motion/fire MCU** on the probe.
  - **ESP32-H2-DevKitM-1 = unused** (shelved). Note it has no Wi-Fi, BLE/Thread only.

## Constraints

- **Short deadline** — prefer ready-made, well-documented, off-the-shelf solutions over
  custom implementations.
- **First embedded project** for the developer; no prior object-detection experience.
  Favor beginner-friendly libraries, clear wiring, and incremental build steps.
- Keep the bill of materials close to what is already purchased (see below).

## Architecture

### Probe (the shooter)

| Function | Hardware |
|---|---|
| Vision, motion, aiming & fire control | Raspberry Pi 5, 8 GB — drives everything directly; **no separate ESP32 on the probe** |
| Camera | Raspberry Pi Camera Module 3 Standard (Adafruit 5657), 12 MP autofocus, 75° FOV, CSI |
| Movement (roaming) | Wheeled cart from TU — 4× Dual-Shaft BO DC geared motors |
| Motor driver | H-bridge (e.g. L298N), tank/differential steering (left pair + right pair) |
| Aiming (per original docs) | NEMA17 stepper (X) + TMC2209 driver, servo (Y), MGN12H 400 mm linear rail |
| IR "gun" emitter | 10× TSAL6200 940 nm IR LEDs, driven via AO3407 MOSFETs, modulated at 38 kHz |
| Cooling | Fan for the Pi during inference |
| Levitation effect | Magnets (Eddy-current approach) — aspirational/experimental |
| Power | Dedicated supplies for compute and motors (separate motor battery) |

> **Open item:** the relationship between the wheeled cart (4 BO motors) and the
> linear-rail + stepper/servo aiming system from the original docs is not fully resolved.
> The cart provides gross movement; the stepper/servo were specified for fine aiming.
> Confirm with the developer which is current before building motion code.

### Vest (the target)

| Function | Hardware |
|---|---|
| Controller | ESP32-WROOM-32 DevKitC (dual-core Xtensa; Wi-Fi + Bluetooth Classic + BLE) |
| Hit detection | 10× TSOP38238 (38 kHz IR receiver modules) — front, back, shoulders for ~360° coverage |
| Per-sensor noise filter | ~100 Ω series resistor on VCC + ~4.7 µF cap across VCC–GND (per datasheet) |
| Player feedback (planned) | Buzzer, LED, vibration motor (motor driven via transistor) |
| Power | Li-ion + UPS/charger module, regulated to 3.3 V |

## Software stack

### Object detection (on the Pi 5)

**Current implementation — HSV color detection (prototype):**
`rpiPy/camera_stream.py` currently detects the player using HSV color segmentation for a
**red shirt** — no YOLO required. This runs at full camera rate on the Pi and was chosen to
get the aiming pipeline working before the heavier YOLO integration. Red requires two HSV
ranges because it wraps the hue wheel (0–10° and 170–180°). Detection output: largest
contour above 3000 px², centroid (cx, cy), X/Y offset from frame center as pixels and %.

**Planned upgrade — YOLO person detection:**
- **Library:** Ultralytics YOLO (`pip install ultralytics`).
- **Model:** `yolo26n` (nano). Fallback: `yolo11n`. Pre-trained on **COCO**; detect the
  built-in **`person`** class — no training required.
- **Optimization:** export to **NCNN** format for ARM; ~doubles FPS (~8 FPS stock →
  ~15+ FPS). Command: `yolo export model=yolo26n.pt format=ncnn`.
- **Camera capture:** Picamera2 (CSI). Only the bounding-box **center (cx, cy)** is needed.
- **Vest discrimination:** after YOLO finds a `person`, crop and run an **HSV** color check
  for the vest color (highly saturated, room-rare color). Calibrate under real game lighting.
  Avoids custom model training.

### IR shot protocol (gun → vest)
- Treat the gun as a **TV remote** and the vest as the **receiver**.
- **Vest side (ESP32-WROOM-32):** use the **IRremote** library to decode incoming codes
  from the TSOP receivers and compare to the expected "shot" code. Rejects ambient IR;
  supports multiple gun codes later.
- **Gun side is now on the Raspberry Pi 5** (no probe ESP32). The Pi must generate the
  38 kHz modulated burst on the TSAL6200 LEDs itself — e.g. via `pigpio` hardware PWM,
  LIRC, or a small dedicated 555/oscillator circuit gated by a GPIO. **Open item:** pick
  the Pi-side IR-generation method; IRremote is Arduino-only and won't run on the Pi.

### Motion control
- ESP32 drives the H-bridge: two direction pins + one **PWM** pin per motor channel
  control direction and speed. **Tie controller ground to motor-battery ground.** Never
  drive motors directly from MCU/Pi pins.

### Communication
- The **vest (ESP32-WROOM-32)** reports hit events to the **Pi 5** (and/or a scoring
  server). The WROOM supports **BLE and Wi-Fi**, so either link works — BLE for a direct
  Pi↔vest link, Wi-Fi if a server/logging is wanted. Pi handles detection and high-level
  game state.

### Scoring server (`scoreApp/`)
- Standalone Flask app (`scoreApp/score_server.py`) that counts IR hits reported by the
  vest over **Wi-Fi**. Has **no camera/picamera2 dependency**, so it runs unchanged on a
  laptop for development and on the Pi 5 for the game. State is **in-memory** and resets on
  restart: `{"active": bool, "hits": int}` — `active` = is a game running, `hits` = score
  (just the hit count; lower is better in this dodge game). Comprehensive logging on every
  event (hits, debounced/ignored hits, start/stop, SSE connect/disconnect, errors).
- **Game lifecycle (start/stop architecture).** The score app is the **game controller**;
  the operator drives it from the web page:
  - `POST /start` → set `active=true`, reset `hits` to 0, and call `start_probe()` to tell
    the probe to begin hunting. The page swaps from the **home screen** to the **scoreboard**.
  - `POST /stop` → set `active=false` and call `stop_probe()` to stand the probe down. The
    page returns to the **home screen** (final score preserved).
  - `start_probe()` / `stop_probe()` make a real `POST` to the probe server at `PROBE_URL`
    (env var, default `http://127.0.0.1:5000`). The probe's `/start` and `/stop` endpoints
    flip a `threading.Event` (`_game_active`) that controls the game loop. Network failures
    are logged and swallowed so an offline probe never crashes the score app.
  - **Hits are only counted while `active`.** A `POST /hit` when no game is running is logged
    and ignored — the vest can't rack up points before the operator starts a round.
- **The web page shows exactly one screen at a time**, chosen by `active`: the home screen
  (START GAME button) when deactivated, the scoreboard (live count, RESET ROUND, STOP GAME)
  when activated. The correct screen is rendered **server-side** on load (no flash) and kept
  in sync **live via SSE**.
- **Endpoints:** `POST /start`, `POST /stop`, `POST /hit` (0.3 s debounce so one shot counts
  once; only while active), `POST /reset` (zero the count, stay active), `GET /score` (JSON
  state), `GET /events` (SSE stream of state), `GET /`. Listens on **port 5001** so it
  coexists with the camera app on port 5000.
- **Run it:**
  ```bash
  pip install -r scoreApp/requirements.txt   # one-time
  python scoreApp/score_server.py            # serves on 0.0.0.0:5001
  ```
  The vest then POSTs to `http://<pi-ip>:5001/hit`.

### Probe software (`rpiPy/`)

Three files, each with a single responsibility:

| File | Role |
|---|---|
| `camera_stream.py` | Flask server (port 5000): camera capture, HSV red-shirt detection, MJPEG stream, game loop, UPS page |
| `hardware.py` | Hardware abstraction for pan/tilt servos + firing mechanism — **currently mocked** (print stubs with TODO comments); replace each function body when hardware is wired |
| `ups_monitor.py` | INA219 I2C driver for the Waveshare UPS Module 3S (I2C bus 1, addr `0x41`); background thread polls every 2 s; safe to call even if UPS is absent |

**`camera_stream.py` internals:**
- **Three daemon threads:** `_capture_loop` (grab + process frames), `_game_loop` (20 Hz
  P-controller), and the UPS poller started by `ups_monitor.start()`.
- **`_target_state` dict** (lock-protected): written by `_process()` every frame with
  `{detected, centered, off_x, off_y}`. Read by `_game_loop` to decide servo/fire actions.
- **Game loop logic:** while `_game_active` is set → if target detected but not centered,
  nudge pan/tilt servos proportionally (`_K_PAN = 0.05`, `_K_TILT = 0.05` deg/px); if
  centered, call `hardware.fire()` with a 1-second cooldown. On stop: `hardware.home()`.
- **`hardware.py` swap points:** each function has a single `# TODO:` line showing exactly
  which driver call to drop in (e.g. `pwm.set_angle(PAN_CHANNEL, angle)`). `_K_PAN` and
  `_K_TILT` in `camera_stream.py` will need tuning once real servos are connected.
- **Flask routes:** `GET /` (camera viewer), `GET /video_feed` (MJPEG), `POST /start`,
  `POST /stop`, `GET /ups`, `GET /ups/data`.

### Vest firmware (`vest/`)
- Arduino sketch for the **ESP32-WROOM-32** (`vest/vest.ino`). On a "hit" it connects to
  Wi-Fi and sends `POST http://<server>:5001/hit` to the score app. Hardware needed for this
  path is only the **board + a data USB cable** — no resistors/TSOP required yet, since the
  hit is currently faked.
- **Current loop is a stand-in:** it auto-fires a hit every 2 s (`reportHit(); delay(2000)`)
  to exercise the scoring pipeline before IR hardware exists. The real version restores
  edge-detection on a pin (button now, TSOP38238 later) with the same 300 ms debounce that
  mirrors the server's. The button + debounce code is kept in the file for that swap.
- **Credentials live in `vest/secrets.h`** (`WIFI_SSID`, `WIFI_PASS`, `SERVER_HOST`,
  `SERVER_PORT`) — **git-ignored**. Copy `vest/secrets.example.h` → `vest/secrets.h` and fill
  in values. The ESP32 has no OS env vars; `secrets.h` is the standard pattern and also works
  in Wokwi.
- **Simulating the vest** (no hardware): a real ESP32 on the same LAN is simplest (the Pi IP
  just works). Web Wokwi (wokwi.com) can't reach a LAN/localhost server — it needs an ngrok
  tunnel. **Wokwi for VS Code + the `wokwigw` gateway** gives true LAN access and compiles
  locally (no cloud compile queue). A pure-Python stand-in (`POST /hit` in a loop) tests the
  server without any ESP at all.

## Control flow between processors
- **Score app (game controller, on the Pi):** the operator starts/stops a round from its web
  page. `POST /start` activates scoring, resets hits to 0, and calls `start_probe()` which
  POSTs to the probe server at port 5000. `POST /stop` deactivates and calls `stop_probe()`.
  Hits only count while a round is active.
- **Pi 5 (probe):** `camera_stream.py` runs on port 5000. `/start` sets `_game_active`; the
  game loop (20 Hz) reads `_target_state` from the vision thread and drives hardware via
  `hardware.py` (currently mocked — servos/fire not yet wired). `/stop` clears `_game_active`
  and calls `hardware.home()`. Current detection: HSV red-shirt; planned: YOLO `person`.
- **Vest ESP32-WROOM-32:** detects hits, runs local feedback, reports each hit to the score
  app over Wi-Fi (`POST /hit`).

## Suggested build order (de-risked, incremental)

**Vest:**
1. One TSOP + ESP32-WROOM-32 → print "HIT" on serial when it sees the gun.
2. Gun side: TSAL6200 + IRremote sending a shot code; vest decodes and validates it.
3. Add feedback (buzzer/LED/vibration).
4. Scale to 10 TSOPs (OUT lines may be tied together if only hit/no-hit is needed).
5. Add BLE reporting.
6. Add battery + make wearable.

**Detection:**
1. `pip install ultralytics`; run `yolo26n` on a test image; confirm it detects a person.
2. Export to NCNN.
3. Picamera2 capture loop filtering for `person`; print box center.
4. (Optional) add HSV vest-color filter.
5. Hand the box center to the motion controller.

## People
- **Martin Velchev** (11В) — vest documentation/subsystem.
- **Aleksandra Stavreva** (11В) — probe/shooter documentation/subsystem.
- **Tsvetelin Marinov** — instructor.

## Glossary
- **TSOP38238** — 38 kHz IR receiver module; OUT pin is active-LOW on valid signal.
- **TSAL6200** — 940 nm IR emitter LED used for the "shots."
- **NCNN** — ARM-optimized neural-network inference format; faster than PyTorch on the Pi.
- **COCO** — public dataset of 80 common object classes the model is pre-trained on.
- **H-bridge** — motor driver circuit enabling direction + speed control (e.g. L298N).
- **Differential/tank steering** — steer a 4-wheel platform by varying left-vs-right speed.
