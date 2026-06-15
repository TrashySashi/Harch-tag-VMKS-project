# AGENTS.md — HArch Tag

Context for AI agents working on the HArch Tag project. Read this before generating
code, wiring, or documentation so suggestions match the actual hardware and design decisions.

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
- **Library:** Ultralytics YOLO (`pip install ultralytics`).
- **Model:** `yolo26n` (nano). Fallback: `yolo11n` if needed. Both use the pre-trained
  **COCO** dataset; detect the built-in **`person`** class — no training required.
- **Optimization:** export the model to **NCNN** format (`yolo export ... format=ncnn`)
  for ARM; roughly doubles FPS. Expect ~8 FPS stock, ~15+ FPS with NCNN on Pi 5 CPU.
- **Camera capture:** Picamera2 (CSI). Per-frame output is the person bounding box; only
  the box **center (cx, cy)** is needed for aiming.
- **Optional vest discrimination by color:** after YOLO finds a `person`, crop to the box
  and run an **HSV color check** for the vest color (highly saturated, room-rare color
  e.g. safety orange / hot magenta / lime green). Calibrate the HSV range under real game
  lighting. This avoids custom model training.

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

## Control flow between processors
- **Pi 5 (probe):** camera capture + YOLO detection → computes target position → drives
  the motion system (H-bridge for the cart motors, stepper/servo for aiming) **and** fires
  the IR gun, all from the Pi's own GPIO.
- **Vest ESP32-WROOM-32:** detects hits, runs local feedback, reports score to the Pi
  over BLE or Wi-Fi.

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
