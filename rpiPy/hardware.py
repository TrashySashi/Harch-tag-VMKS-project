"""
Hardware abstraction for pan/tilt servos and firing mechanism.

MOCKED — replace each function body with real driver calls once hardware is
connected. Angles are degrees relative to home (0, 0).

Pan/tilt driver options (pick one when ready):
  - Direct GPIO PWM:    RPi.GPIO or gpiozero Servo
  - I2C servo driver:   PCA9685 via adafruit-circuitpython-pca9685

Fire mechanism options:
  - GPIO relay/solenoid: GPIO.output(FIRE_PIN, HIGH/LOW)
  - Trigger servo:       sweep from rest angle to pull angle
"""

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


def fire() -> None:
    """Trigger one firing pulse."""
    # TODO: GPIO.output(FIRE_PIN, GPIO.HIGH); time.sleep(0.05); GPIO.output(FIRE_PIN, GPIO.LOW)
    print("[MOCK hw] FIRE")


def stop_fire() -> None:
    """Ensure the firing mechanism is disengaged."""
    # TODO: GPIO.output(FIRE_PIN, GPIO.LOW)
    print("[MOCK hw] fire OFF")


def home() -> None:
    """Return servos to centre and disengage firing."""
    set_pan(0.0)
    set_tilt(0.0)
    stop_fire()
