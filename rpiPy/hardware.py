"""
Hardware abstraction for pan/tilt servos and firing mechanism.

MOCKED — replace each function body with real driver calls once hardware is
connected. Angles are degrees relative to home (0, 0).

`fire()` is still a mock (no IR LED is pulsed yet), but it DOES report the shot
to the score app over HTTP (POST /shot) so the scoring pipeline can be exercised
end-to-end before the firing hardware exists. The score app's address is read
from a .env file (SCORE_URL); copy .env.example to .env to configure it.

Pan/tilt driver options (pick one when ready):
  - Direct GPIO PWM:    RPi.GPIO or gpiozero Servo
  - I2C servo driver:   PCA9685 via adafruit-circuitpython-pca9685

Fire mechanism options:
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

_PAN_MIN,  _PAN_MAX  = -90.0, 90.0   # degrees; negative = left
_TILT_MIN, _TILT_MAX = -45.0, 45.0   # degrees; negative = up


def set_pan(angle: float) -> None:
    """Drive pan servo to *angle* degrees."""
    angle = max(_PAN_MIN, min(_PAN_MAX, angle))
    # TODO: pwm.set_angle(PAN_CHANNEL, angle)
    print(f"[MOCK hw] pan  → {angle:+.1f}°")


def set_tilt(angle: float) -> None:
    """Drive tilt servo to *angle* degrees."""
    angle = max(_TILT_MIN, min(_TILT_MAX, angle))
    # TODO: pwm.set_angle(TILT_CHANNEL, angle)
    print(f"[MOCK hw] tilt → {angle:+.1f}°")


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
    """Return servos to centre and disengage firing."""
    set_pan(0.0)
    set_tilt(0.0)
    stop_fire()
