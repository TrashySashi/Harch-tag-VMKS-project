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

`fire()` pulses the IR "gun": a GPIO drives the MOSFET gate that switches the
10x TSAL6200 940 nm LED array, modulated at 38 kHz by the Pi's hardware-PWM
peripheral (rpi-hardware-pwm, sysfs). One "shot" = hold that carrier on for a
short burst, then off. The TSOP38238 on the vest pulls its OUT line low for the
duration of the burst and the vest reports one hit per falling edge, so a plain
38 kHz burst is enough to register a hit — no NEC/remote code is required.
`fire()` also reports the shot to the score app over HTTP (POST /shot) so the
scoring pipeline stays in sync. The score app's address is read from a .env file
(SCORE_URL); copy .env.example to .env to configure it.

If the hardware PWM can't be initialised (e.g. developing off-Pi, or the pwm
overlay isn't enabled), fire()/stop_fire() fall back to printing `[MOCK hw]` so
the rest of the pipeline still runs — same pattern as the cart drive below.
"""

import logging
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("harchtag.hardware")

# If nothing has configured logging yet (e.g. running this module standalone for
# IR diagnostics via `python -c "import hardware; hardware.ir_test()"`), set up a
# basic INFO handler so the import-time and fire() logs are actually visible.
# When imported by camera_stream.py its basicConfig wins and this is a no-op.
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

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

# --- IR gun (38 kHz modulated burst, hardware PWM) -----------------------
# The IR pin drives the MOSFET gate switching the TSAL6200 array. It MUST be a
# hardware-PWM line so the 38 kHz carrier is jitter-free. On the Pi 5 the PWM
# lines are GPIO 12/13/18/19; the cart drive already uses 13 and 18 (EN pins),
# so the gun uses GPIO 12 = PWM channel 0. Wire the MOSFET gate to GPIO 12.
#
# Enable the PWM overlay once on the Pi (then reboot). This both creates the
# sysfs pwmchip AND muxes the GPIO to its PWM function — without it the carrier
# toggles in sysfs but never reaches the physical pin, so the LEDs stay dark:
#   echo "dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4" \
#        | sudo tee -a /boot/firmware/config.txt
# and install the lib:  pip install rpi-hardware-pwm
#
# The sysfs pwmchip *number* is not stable across boards/kernels (Pi 4 was 0,
# some Pi 5 images expose the RP1 PWM as 2, the current 6.18 image as 0, and it
# can shift again after adding the overlay). So we auto-detect: try the hint
# below first, then fall back to whichever /sys/class/pwm/pwmchipN actually
# exists. Set the hint to your known-good chip to skip the scan.
_IR_PWM_CHANNEL  = 0      # 0 -> GPIO 12, 1 -> GPIO 13, 2 -> GPIO 18, 3 -> GPIO 19
_IR_PWM_CHIP_HINT = 0     # preferred pwmchip number; auto-detect falls back if absent
_IR_FREQ_HZ      = 38000  # TSOP38238 carrier frequency
_IR_DUTY         = 33     # carrier duty cycle (%) — ~1/3 per IR LED datasheets
_IR_BURST_S      = 0.50   # how long to hold the carrier on per shot


def _detect_pwm_chip(hint: int) -> int | None:
    """Return a pwmchip number that exists in sysfs, or None if there are none.

    Prefers *hint* when ``/sys/class/pwm/pwmchip{hint}`` is present; otherwise
    returns the lowest-numbered pwmchip found. None means no PWM is exposed at
    all (overlay missing / off-Pi), which the caller treats as "mock the gun".
    """
    if os.path.isdir(f"/sys/class/pwm/pwmchip{hint}"):
        return hint
    try:
        chips = sorted(
            int(n[len("pwmchip"):])
            for n in os.listdir("/sys/class/pwm")
            if n.startswith("pwmchip") and n[len("pwmchip"):].isdigit()
        )
    except OSError:
        return None
    return chips[0] if chips else None

try:
    from rpi_hardware_pwm import HardwarePWM

    _IR_PWM_CHIP = _detect_pwm_chip(_IR_PWM_CHIP_HINT)
    if _IR_PWM_CHIP is None:
        raise RuntimeError(
            "no /sys/class/pwm/pwmchipN found — add the dtoverlay above and reboot"
        )
    if _IR_PWM_CHIP != _IR_PWM_CHIP_HINT:
        log.info("PWM chip hint %d absent; auto-detected pwmchip%d",
                 _IR_PWM_CHIP_HINT, _IR_PWM_CHIP)

    _ir = HardwarePWM(pwm_channel=_IR_PWM_CHANNEL, hz=_IR_FREQ_HZ, chip=_IR_PWM_CHIP)
    _ir.start(0)          # carrier configured, output off (0% duty) until we fire
    _MOCK_FIRE = False
    log.info("IR gun ready (hardware PWM %d kHz, chip %d, channel %d)",
             _IR_FREQ_HZ, _IR_PWM_CHIP, _IR_PWM_CHANNEL)
except Exception as e:    # rpi-hardware-pwm missing, overlay off, or off-Pi → mock
    _ir = None
    _MOCK_FIRE = True
    log.warning("IR gun mocked (hardware PWM unavailable: %s)", e)


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
    """Fire one IR shot — a 38 kHz burst — and report it to the score app.

    Blocks for ``_IR_BURST_S`` while the carrier is on (well under the game
    loop's tolerance). Off-Pi this is a ``[MOCK hw]`` print.
    """
    if _MOCK_FIRE:
        print("[MOCK hw] FIRE")
        log.warning("fire() ran in MOCK mode — no IR carrier emitted "
                    "(hardware PWM unavailable at import time)")
    else:
        log.info("fire(): carrier ON  ch=%d chip=%d freq=%dHz duty=%d%% burst=%.3fs",
                 _IR_PWM_CHANNEL, _IR_PWM_CHIP, _IR_FREQ_HZ, _IR_DUTY, _IR_BURST_S)
        _ir.change_duty_cycle(_IR_DUTY)   # carrier ON → IR LEDs pulse at 38 kHz
        time.sleep(_IR_BURST_S)
        _ir.change_duty_cycle(0)          # carrier OFF
        log.info("fire(): carrier OFF (burst complete)")
    _report_shot()


def ir_test(duration_s: float = 3.0) -> None:
    """Diagnostic: hold the IR carrier ON for *duration_s* so you can physically
    confirm the diode emits — point a phone camera at the LED array and look for
    a faint purple glow (most phone cameras pass 940 nm IR). The game loop's real
    burst is only ``_IR_BURST_S`` (~0.1 s), far too short to eyeball.

    Run from a REPL on the Pi:  ``python -c "import hardware; hardware.ir_test()"``
    """
    if _MOCK_FIRE:
        print("[MOCK hw] IR TEST (no real carrier — hardware PWM unavailable)")
        log.warning("ir_test() ran in MOCK mode — IR gun was not initialised")
        return
    log.info("ir_test(): carrier ON for %.1fs  ch=%d chip=%d freq=%dHz duty=%d%%",
             duration_s, _IR_PWM_CHANNEL, _IR_PWM_CHIP, _IR_FREQ_HZ, _IR_DUTY)
    _ir.change_duty_cycle(_IR_DUTY)
    time.sleep(duration_s)
    _ir.change_duty_cycle(0)
    log.info("ir_test(): carrier OFF")


def stop_fire() -> None:
    """Ensure the IR carrier is off (safe idle state)."""
    if _MOCK_FIRE:
        print("[MOCK hw] fire OFF")
        return
    _ir.change_duty_cycle(0)


def home() -> None:
    """Stop the cart and disengage firing (safe idle state)."""
    stop_drive()
    stop_fire()
