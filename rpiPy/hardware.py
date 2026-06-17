"""
Hardware abstraction for the probe: cart movement (aiming) + firing mechanism.

The camera is mounted on the SIDE of the cart, so horizontal aiming is done by
rotating the whole 4-wheel cart in place rather than panning a servo. There is
no vertical (tilt) axis — gun elevation is fixed. The cart uses tank/differential
steering: the two LEFT wheels move together and the two RIGHT wheels move
together, driven by an L298N H-bridge through gpiozero.

Rotation strategy = PIVOT (drive one side, the other stays stopped):
  - clockwise   → target is to the LEFT of the frame  → drive LEFT side forward
  - anti-clock  → target is to the RIGHT of the frame → drive RIGHT side forward
If the cart turns the wrong way for your wiring, flip ``_INVERT_ROTATION``.

If gpiozero (or its Pi pin backend) isn't available — e.g. when developing on a
laptop — the drive functions fall back to printing ``[MOCK hw]`` so the rest of
the pipeline still runs.

`fire()` is still a mock (no IR LED is pulsed yet), but it DOES report the shot
to the score app over HTTP (POST /shot) so the scoring pipeline can be exercised
end-to-end before the firing hardware exists. The score app's address is read
from a .env file (SCORE_URL); copy .env.example to .env to configure it.

Fire mechanism options (pick one when ready):
  - GPIO relay/solenoid: GPIO.output(FIRE_PIN, HIGH/LOW)
  - Trigger servo:       sweep from rest angle to pull angle
"""

import logging
import os

import requests
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("harchtag.hardware")

# Score app base URL (port 5001). Configured via .env so the address isn't
# hard-coded. e.g. SCORE_URL=http://127.0.0.1:5001
SCORE_URL = os.getenv("SCORE_URL", "http://127.0.0.1:5001").rstrip("/")
SCORE_TIMEOUT_S = float(os.getenv("SCORE_TIMEOUT_S", "3"))

# --- Cart drive (L298N via gpiozero) -------------------------------------
# L298N pin map in BCM numbering. Each channel = 2 direction pins + 1 PWM
# enable pin. Adjust these to match your wiring (EN pins should be PWM-capable;
# 18 and 13 are hardware-PWM lines on the Pi). Avoid the I2C pins (GPIO 2/3)
# used by the UPS monitor.
# _LEFT_IN1,  _LEFT_IN2,  _LEFT_EN  = 23, 24, 13   # left wheel pair
# _RIGHT_IN1, _RIGHT_IN2, _RIGHT_EN = 27, 17, 18   # right wheel pair

_LEFT_IN1,  _LEFT_IN2,  _LEFT_EN  = 23, 24, 13   # left wheel pair
_RIGHT_IN1, _RIGHT_IN2, _RIGHT_EN = 27, 17, 18  

# Fixed rotation speed (PWM duty, 0.0–1.0) for bang-bang aiming.
_DRIVE_SPEED = 0.6

# Set True if the cart spins the wrong way relative to the target side.
_INVERT_ROTATION = False

try:
    from gpiozero import Motor

    _left = Motor(forward=_LEFT_IN1, backward=_LEFT_IN2, enable=_LEFT_EN)
    _right = Motor(forward=_RIGHT_IN1, backward=_RIGHT_IN2, enable=_RIGHT_EN)
    _MOCK_DRIVE = False
    log.info("cart drive ready (L298N via gpiozero)")
except Exception as e:  # gpiozero missing or not on a Pi → mock the drive
    _left = _right = None
    _MOCK_DRIVE = True
    log.warning("cart drive mocked (gpiozero unavailable: %s)", e)


def _pivot(direction: str) -> None:
    """Pivot the cart by driving ONE side forward; the other side stays stopped.

    *direction* is ``"cw"`` (clockwise) or ``"ccw"`` (anti-clockwise).
    """
    if _INVERT_ROTATION:
        direction = "ccw" if direction == "cw" else "cw"

    if _MOCK_DRIVE:
        print(f"[MOCK hw] rotate {direction.upper()}")
        return

    if direction == "cw":
        _left.forward(_DRIVE_SPEED)
        _right.stop()
    else:
        _right.forward(_DRIVE_SPEED)
        _left.stop()


def rotate_cw() -> None:
    """Spin the cart clockwise (target is to the LEFT of the frame)."""
    _pivot("cw")


def rotate_ccw() -> None:
    """Spin the cart anti-clockwise (target is to the RIGHT of the frame)."""
    _pivot("ccw")


def stop_drive() -> None:
    """Stop both wheel pairs."""
    if _MOCK_DRIVE:
        print("[MOCK hw] drive STOP")
        return
    _left.stop()
    _right.stop()


def _report_shot() -> None:
    """Tell the score app the probe fired a shot (POST /shot).

    Failures are logged but never raise — a score app that's offline must not
    break the probe's game loop.
    """
    url = f"{SCORE_URL}/shot"
    try:
        resp = requests.post(url, timeout=SCORE_TIMEOUT_S)
        log.info("shot -> %s %d %s", url, resp.status_code, resp.text.strip())
    except requests.RequestException as e:
        log.warning("shot report failed (%s): %s", url, e)


def fire() -> None:
    """Trigger one firing pulse and report the shot to the score app."""
    # TODO: GPIO.output(FIRE_PIN, GPIO.HIGH); time.sleep(0.05); GPIO.output(FIRE_PIN, GPIO.LOW)
    print("[MOCK hw] FIRE")
    _report_shot()


def stop_fire() -> None:
    """Ensure the firing mechanism is disengaged."""
    # TODO: GPIO.output(FIRE_PIN, GPIO.LOW)
    print("[MOCK hw] fire OFF")


def home() -> None:
    """Stop the cart and disengage firing (safe idle state)."""
    stop_drive()
    stop_fire()
