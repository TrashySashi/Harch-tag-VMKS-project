"""Isolated L298N motor test — no camera, no detection.

Drives the LEFT pair, then the RIGHT pair, forward then backward at full
speed so you can confirm the motors physically respond. LIFT THE CART so the
wheels are off the ground before running.

Run from the rpiPy folder:
    pkill -f camera_stream.py      # free the GPIO pins first
    python3 motor_test.py
"""
import time
from gpiozero import Motor

# Same pin map as hardware.py
LEFT  = Motor(forward=23, backward=24, enable=13)
RIGHT = Motor(forward=27, backward=17, enable=18)

def run(name, m):
    print(f"\n=== {name}: FORWARD full speed (3s) ===")
    m.forward(1.0); time.sleep(3); m.stop()
    print(f"=== {name}: BACKWARD full speed (3s) ===")
    m.backward(1.0); time.sleep(3); m.stop()
    print(f"=== {name}: stopped ===")

try:
    run("LEFT pair (EN=GPIO13)",  LEFT)
    run("RIGHT pair (EN=GPIO18)", RIGHT)
finally:
    LEFT.stop(); RIGHT.stop()
    print("\nDone. Did the wheels turn?")
