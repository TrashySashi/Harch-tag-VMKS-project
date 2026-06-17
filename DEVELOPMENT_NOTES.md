# HArch Tag — Development Notes

A running log of project context, decisions, and hardware/software setup learned while
building HArch Tag. Pairs with `AGENTS.md` (which holds the structured project spec). This
file is the narrative "how we got here / what we figured out" companion.

---

## 1. Project summary

HArch Tag is a **single-player laser-tag game** inspired by the *Marksman-H training remote*
from Star Wars. An autonomous device hunts the player and fires modulated infrared "laser"
shots; the player tries to dodge them. School project for "Вградени микрокомпютърни системи –
Практика" (Embedded Microcomputer Systems – Practice).

Two physical components that talk wirelessly:

- **Probe (the shooter):** a moving platform (4-wheel BO-motor cart) carrying a Raspberry Pi
  5 + Camera Module 3. The Pi runs object detection to find the player, aims, and fires an
  IR LED array (TSAL6200). The Pi drives everything directly.
- **Vest (the target):** worn by the player, with 10 TSOP IR receivers arrayed across the
  **chest (front only)** that detect hits, run by an ESP32-WROOM-32 DevKitC, with local
  feedback and wireless score reporting.

Core loop: **camera sees player → Pi detects + aims → fires IR → vest registers the hit.**

---

## 2. Key decisions (and why)

| Decision | Reasoning |
|---|---|
| **Normal RGB camera + object detection** instead of an IR camera | Locate the player visually with a ready-made model; simpler, no IR beacon needed. |
| **Dropped the lightsaber component** | Reduce scope to fit a short deadline. |
| **Removed the vest's IR beacon LEDs (VSMB1940X01)** | They only existed to make the vest visible to an IR camera; the normal camera makes them unnecessary. |
| **Use a pre-trained model (no custom training)** | Short deadline + first-time developer. Detect the COCO "person" class out of the box. |
| **Software-only detection on the Pi 5** (no Hailo/Coral accelerator) | Keep cost and complexity down; ~8–15 FPS is enough for the game. |
| **Optional vest discrimination by color** (HSV check inside the person box) | Avoids the heavy work of training a custom vest detector. |
| **Vest controller = ESP32-WROOM-32 DevKitC** | The board actually on hand; has Wi-Fi + Bluetooth + BLE. |
| **Probe controlled directly by the Raspberry Pi 5** (no probe ESP32) | Simpler architecture; Pi handles vision, motion, aiming, and firing. |
| **ESP32-H2-DevKitM-1 shelved** | Not needed once the WROOM took the vest role; also has no Wi-Fi. |

---

## 3. Object detection plan

- **Library:** Ultralytics YOLO (`pip install ultralytics`).
- **Model:** `yolo26n` (nano); fallback `yolo11n`. Pre-trained on **COCO**; detect the
  built-in **`person`** class. No training required.
- **Speed optimization:** export to **NCNN** format for ARM — roughly doubles FPS on the Pi
  (~8 FPS stock → ~15+ FPS). Command pattern: `yolo export model=yolo26n.pt format=ncnn`.
- **Camera capture:** Picamera2 on the Pi (CSI). Only the bounding-box **center (cx, cy)**
  is needed for aiming.
- **Optional vest color filter:** after YOLO finds a `person`, crop to the box and run an
  **HSV** color check for a highly-saturated, room-rare vest color (safety orange / hot
  magenta / lime green). Calibrate the HSV range under real game lighting.
- **Develop without the Pi:** YOLO runs on any laptop. Build/test the whole detection
  pipeline now using a webcam or sample videos; the only line that changes on the Pi is the
  camera-capture call.

---

## 4. Vest build plan (incremental, de-risked)

1. One TSOP + ESP32-WROOM-32 → print "HIT" on serial when it sees the gun.
2. Gun side: TSAL6200 + 38 kHz modulated code; vest decodes and validates the "shot" code.
3. Add local feedback (buzzer / LED / vibration motor).
4. Scale to 10 TSOPs in a **chest array (front only)** — a 3–4–3 grid (or similar even
   spacing) spanning shoulder-to-shoulder × collarbone-to-sternum, all domes forward, the
   left/right columns angled ~30° outward to catch slightly off-axis shots. OUT lines may be
   tied together since only hit/no-hit matters. (360°/back/shoulder coverage was dropped to
   simplify the build.)
5. Add wireless score reporting (BLE or Wi-Fi) to the Pi/server.
6. Add battery + make wearable (regulated 3.3 V).

**IR = a TV remote.** The gun is the remote, the vest is the receiver. The TSOP only reacts
to IR modulated at **38 kHz** and ignores ambient light. On the vest, the **IRremote** library
decodes the code. Per the TSOP datasheet, add a noise filter on each sensor's power pin
(~100 Ω series + ~4.7 µF cap).

> **Open item:** the gun-side IR firing now lives on the Pi (no probe ESP32). IRremote is
> Arduino-only, so the Pi must generate the 38 kHz burst another way — e.g. `pigpio` hardware
> PWM, LIRC, or a small 555/oscillator circuit gated by a GPIO.

---

## 5. Motors / cart notes

- The cart uses **Dual-Shaft BO DC geared motors**. Wheels are a **push-fit** onto the
  flattened (oval/double-D) shaft — the wheel hole must match the shaft profile.
  - "Won't go in fully" is usually: shaft bottoming out in a blind hole, the flats not
    aligned, or molding flash inside the hole. Don't force it — the gearbox is plastic.
- **You cannot drive motors directly from the Pi/ESP pins.** Use a **motor driver / H-bridge**
  (e.g. L298N) + a **separate motor battery**, and **tie all grounds together**.
- Steering: wire the left pair and right pair together → **differential/tank steering**.
  Direction = two pins per channel; speed = a **PWM** signal.

---

## 6. Hardware setup learnings (ESP32-WROOM-32 DevKitC)

**Arduino IDE board selection:** don't pick from the "wroom"-filtered list. Clear the search
and choose **"ESP32 Dev Module"** — the generic, correct option for a classic WROOM-32 DevKitC.

**Port selection:** the COM ports in the IDE are *virtual serial ports*, not physical USB
sockets — having one physical port but seeing two COM entries is normal (the extra is often
Bluetooth). Identify the board's port by **unplugging it and seeing which COM disappears**,
or via Device Manager → Ports → "Silicon Labs CP210x" (or "CH340" on clones).

**Cable:** must be a **data** cable, not charge-only (charge-only = board lights up but no COM
port appears). A **USB-C-to-USB-A** cable into a normal USB port is the most reliable — it
sidesteps the missing-CC-resistor problem some cheap USB-C boards have with C-to-C cables.

**Drivers:** if no COM port appears, install the **CP210x** (or CH340) USB-serial driver.

**Upload tip:** if it stalls on "Connecting…", hold the **BOOT** button until upload starts.

---

## 7. Testing without resistors (important safety note)

- **An LED needs a current-limiting resistor.** Driving one straight off a 3.3 V pin can
  destroy the LED and overstress the GPIO (safe ~10–12 mA per pin, 40 mA absolute max).
- **A Darlington transistor does NOT replace a resistor** — it's a switch/amplifier, not a
  current limiter. (It's useful later for switching the vibration motor, with a base resistor
  and flyback diode.)
- **PWM does not make it safe** — it only lowers the *average* current; peak current per pulse
  is still uncontrolled.
- **Safe no-resistor test:** use the **Serial Monitor** (no components needed), and/or blink
  the **onboard LED** (it has its own resistor on the board). This validates board + cable +
  driver + upload pipeline with zero risk.
- To actually light an external LED later: scavenge a 220 Ω–1 kΩ resistor from old electronics,
  use an LED module with a built-in resistor, or a potentiometer as a stand-in.

---

## 8. Simulation strategy (build software before the parts arrive)

- **Vest firmware → Wokwi** (free, browser-based). Its default ESP32 board *is* the WROOM-32,
  and it runs real Arduino code. No TSOP part exists in Wokwi, so use a **pushbutton as a
  stand-in for the hit signal** — the hit-counting/feedback/scoring logic is identical, and
  the code transfers to hardware unchanged. Start: https://wokwi.com/esp32
- **Detection → develop on a laptop** with a webcam/sample videos; transfers to the Pi later.
- **Tinkercad Circuits** is good for *learning* breadboard wiring + why the resistor matters,
  but only simulates the Arduino Uno (not the ESP32).
- **Can't be meaningfully simulated:** real IR optics/range/timing, motor mechanics/current,
  magnetic levitation, true wireless range, and real Pi frame rate. Those need the parts.

---

## 9. Mini-glossary (concepts learned)

- **`Serial.begin(115200)`** — opens the USB text channel between board and PC at 115200 baud
  (bits/sec). The Serial Monitor's baud must match or the text is garbled.
- **`pinMode(pin, OUTPUT)`** — configures a pin's direction. `OUTPUT` = the ESP drives it
  (LED, buzzer, IR LED). `INPUT` / `INPUT_PULLUP` = the ESP reads it (button, TSOP sensor).
- **A microcontroller runs its program whenever powered**, and re-runs the *last uploaded*
  program on every power-up. It holds only **one** program; uploading **overwrites** the
  previous one (no on-board history/undo). "Do nothing" = upload an empty `setup()`/`loop()`.
- **Safe GPIOs for output (WROOM-32):** 4, 5, 16, 17, 18, 19, 21, 22, 23, 25, 26, 27, 32, 33.
  Avoid 6–11 (flash), 34–39 (input-only), and strapping pins 0/2/12/15 for finicky uses.
- **Unplugging** is fine anytime *except mid-upload*; the ESP32 isn't a storage device, so no
  "safe eject" is needed. Grip the connector, not the cable.

---

## 11. What is actually implemented (rpiPy/)

### camera_stream.py — Flask probe server (port 5000)

Fully working on the Pi. Three daemon threads run concurrently:

- **`_capture_loop`** — grabs frames from the Pi Camera Module 3 via Picamera2 (1280×720,
  30 fps, RGB888). Calls `_process()` on each frame and stores the result in `_latest_frame`.
- **`_game_loop`** — 20 Hz P-controller. Reads `_target_state` (written by `_process`) and
  drives hardware:
  - Target detected and not centered → nudge pan/tilt angles by `off_x * _K_PAN` /
    `off_y * _K_TILT` and call `hardware.set_pan()` / `hardware.set_tilt()`.
  - **Firing (MOCK):** `hardware.fire()` is called every `_FIRE_INTERVAL` (2 s) while a game
    runs, regardless of centering, so the scoring pipeline works without real hardware. A
    code comment marks where to restore centred-only firing once the gun exists.
  - Game stopped → `hardware.home()` (servos center, fire off).
- **UPS poller** (started by `ups_monitor.start()`) — reads INA219 every 2 s.

The `_target_state` dict (`{detected, centered, off_x, off_y}`) is the handoff point between
the vision thread and the game loop. It is written under `_target_lock` in `_process()` at
every possible code path (no-target early returns and the success path).

Detection pipeline in `_process()`:
1. BGR → HSV conversion.
2. Two-range red mask (hue wraps: 0–10° and 170–180°).
3. Morphological open + dilate to remove noise.
4. Largest contour above 3 000 px² → centroid → X/Y offset from frame center.
5. Draws green contour, yellow centroid, crosshair, deadzone box, orange directional arrows
   or "CENTERED" label. Writes offset % to bottom-left of frame.

Flask routes: `GET /`, `GET /video_feed` (MJPEG at JPEG quality 80, ~30 fps),
`POST /start`, `POST /stop`, `GET /ups`, `GET /ups/data`.

### hardware.py — abstraction layer for actuators

`set_pan`, `set_tilt`, `stop_fire` print `[MOCK hw]` output and do nothing else; each has a
`# TODO:` comment pointing to the exact driver call to drop in. `home()` is a convenience
wrapper: `set_pan(0) + set_tilt(0) + stop_fire()`.

`fire()` is **partially live**: the IR pulse is still a mock (`print("[MOCK hw] FIRE")`), but
it now also calls `_report_shot()`, which `POST`s to the score app's `/shot` endpoint. The
score app address comes from `rpiPy/.env` (`SCORE_URL`, default `http://127.0.0.1:5001`;
optional `SCORE_TIMEOUT_S`); copy `rpiPy/.env.example` → `rpiPy/.env` to configure it. The
POST failure is logged and swallowed, so an offline score app never stalls the game loop.

Dependencies: `rpiPy/requirements.txt` (flask, opencv-python, numpy, smbus2, requests,
python-dotenv). **picamera2 is not pip-installable on Raspberry Pi OS** — install it via
`sudo apt install -y python3-picamera2`.

**When hardware arrives:** only `hardware.py` needs to change. The game loop and detection
pipeline are hardware-agnostic. Tune `_K_PAN` / `_K_TILT` (currently `0.05` deg/px) in
`camera_stream.py` to eliminate overshoot/oscillation.

### ups_monitor.py — Waveshare UPS Module 3S driver

INA219 on I2C bus 1, address `0x41`. Reads bus voltage, shunt voltage, current, power.
Derives battery % from voltage (9.0 V = 0%, 12.6 V = 100% for 3S Li-ion). Charging detected
when current < −50 mA. Background thread polls every 2 s; `start()` is safe to call even
if the UPS is not connected (prints a warning and returns silently).

### score_server.py — what changed from the original "mocked" description

`start_probe()` / `stop_probe()` were never log-only no-ops — they always POSTed to
`PROBE_URL` (configurable via `.env`). What was missing was the probe-side logic; that is
now implemented. The full call chain is:
```
Browser → POST /start (score_server :5001)
  → start_probe() → POST http://<pi>:5000/start
    → _game_active.set()  (camera_stream.py)
      → _game_loop() begins tracking and firing
        → hardware.fire() every 2 s → POST http://<score>:5001/shot
          → shots/misses update on the scoreboard (SSE)
```

### score_server.py — shots & misses (added)

The scoreboard now tracks **three** figures instead of one:

- `hits` — IR hits the vest registers, reported by the vest via `POST /hit` (unchanged).
- `shots` — shots fired, reported by the probe via the new `POST /shot` endpoint. Same 0.3 s
  debounce as `/hit` (own timestamp `_last_shot_ts`), only counted while a round is active.
- `misses` — `max(0, shots - hits)`. **Not stored** — `_snapshot()` derives it on every call,
  so it is recomputed on each `/hit` and each `/shot`. The hit and shot counts arrive from two
  devices that don't talk to each other, so recomputing from both on every event (rather than
  bumping a separate counter in one place) is what keeps them consistent; the clamp at 0
  absorbs a hit that lands just before its shot is reported.

`/start` and `/reset` zero both counters (and both debounce timestamps). All three numbers
render server-side on load and update live over the existing SSE stream — shots (grey) and
misses (amber) flank the big red hit count.

> **Now wired:** `hardware.fire()` (`rpiPy/hardware.py`) reports each shot to the score app
> via `POST /shot`. While the firing hardware is mocked, the game loop fires every 2 s, so
> `shots`/`misses` populate as soon as a game is started.

---

## 10. Open items / TODO

- [x] Wire score app `start_probe()` / `stop_probe()` to the probe server — **done**
      (real HTTP calls; probe `/start`/`/stop` flip `_game_active` Event).
- [x] Wire the probe to report fired shots — `hardware.fire()` POSTs `/shot` to the score app
      (port 5001) each time it fires (mock fires every 2 s while a game runs).
- [ ] Wire `hardware.py` — replace mock stubs (servos + real IR fire pulse) with real drivers,
      and restore centred-only firing in `_game_loop` (currently a 2 s mock cadence).
- [ ] Tune `_K_PAN` / `_K_TILT` gain constants once servos are connected.
- [ ] Resolve probe motion: wheeled BO-motor cart vs. the linear-rail + stepper/servo aiming
      from the original docs — which is current?
- [ ] Choose the Pi-side 38 kHz IR generation method (pigpio PWM / LIRC / oscillator circuit).
- [ ] Upgrade detection from HSV red-shirt to YOLO `person` + HSV vest-color filter.
- [ ] Pick and physically test the vest color for the HSV filter under game lighting.
- [ ] Source resistors (and the rest of the BOM) for the physical build.
- [ ] Confirm vest power design (regulated 3.3 V, battery capacity for a play session).

---

## People

- **Martin Velchev** (11В) — vest subsystem.
- **Aleksandra Stavreva** (11В) — probe/shooter subsystem.
- **Tsvetelin Marinov** — instructor.
