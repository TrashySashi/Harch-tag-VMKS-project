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
- **Vest (the target):** worn by the player, studded with 10 TSOP IR receivers that detect
  hits, run by an ESP32-WROOM-32 DevKitC, with local feedback and wireless score reporting.

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
4. Scale to 10 TSOPs for ~360° coverage (OUT lines may be tied together if only hit/no-hit
   matters).
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

## 10. Open items / TODO

- [ ] Resolve probe motion: wheeled BO-motor cart vs. the linear-rail + stepper/servo aiming
      from the original docs — which is current?
- [ ] Choose the Pi-side 38 kHz IR generation method (pigpio PWM / LIRC / oscillator circuit).
- [ ] Pick and physically test the vest color for the HSV filter under game lighting.
- [ ] Source resistors (and the rest of the BOM) for the physical build.
- [ ] Confirm vest power design (regulated 3.3 V, battery capacity for a play session).

---

## People

- **Martin Velchev** (11В) — vest subsystem.
- **Aleksandra Stavreva** (11В) — probe/shooter subsystem.
- **Tsvetelin Marinov** — instructor.
