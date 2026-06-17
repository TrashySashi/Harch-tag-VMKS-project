"""
Waveshare UPS Module 3S — INA219 driver
I2C address: 0x41, bus: /dev/i2c-1 (GPIO header)
3S Li-ion: 9.0 V (0%) → 12.6 V (100%)
"""

import smbus2
import struct
import threading
import time

# INA219 registers
_REG_CONFIG      = 0x00
_REG_SHUNT_V     = 0x01
_REG_BUS_V       = 0x02
_REG_POWER       = 0x03
_REG_CURRENT     = 0x04
_REG_CALIBRATION = 0x05

# Configuration: 32 V range, gain ±320 mV, 12-bit, 32-sample average, continuous
_CONFIG_VALUE = 0x3FFF

# Calibration for 2 A max / 0.1 Ω shunt
_CURRENT_LSB = 2.0 / 32768          # A per bit  ≈ 61 µA
_CAL_VALUE   = int(0.04096 / (_CURRENT_LSB * 0.1))   # 6710
_POWER_LSB   = 20.0 * _CURRENT_LSB  # W per bit

_BATT_V_MIN  = 9.0    # 3 × 3.0 V — fully discharged
_BATT_V_MAX  = 12.6   # 3 × 4.2 V — fully charged

I2C_BUS  = 1
I2C_ADDR = 0x41


class INA219:
    def __init__(self, bus: int = I2C_BUS, addr: int = I2C_ADDR):
        self._bus  = smbus2.SMBus(bus)
        self._addr = addr
        self._write16(_REG_CONFIG,      _CONFIG_VALUE)
        self._write16(_REG_CALIBRATION, _CAL_VALUE)

    # ------------------------------------------------------------------ I/O
    def _write16(self, reg: int, value: int):
        data = [(value >> 8) & 0xFF, value & 0xFF]
        self._bus.write_i2c_block_data(self._addr, reg, data)

    def _read16_signed(self, reg: int) -> int:
        raw = self._bus.read_i2c_block_data(self._addr, reg, 2)
        val = (raw[0] << 8) | raw[1]
        return struct.unpack(">h", struct.pack(">H", val))[0]

    def _read16(self, reg: int) -> int:
        raw = self._bus.read_i2c_block_data(self._addr, reg, 2)
        return (raw[0] << 8) | raw[1]

    # ---------------------------------------------------------------- reads
    def bus_voltage(self) -> float:
        """Main battery voltage in V (right-shift 3, scale by 4 mV)."""
        raw = self._read16(_REG_BUS_V)
        return ((raw >> 3) * 0.004)

    def shunt_voltage_mv(self) -> float:
        """Voltage across shunt resistor in mV."""
        return self._read16_signed(_REG_SHUNT_V) * 0.01

    def current_ma(self) -> float:
        """Load current in mA (negative = charging)."""
        return self._read16_signed(_REG_CURRENT) * (_CURRENT_LSB * 1000)

    def power_w(self) -> float:
        """Power in W."""
        return self._read16(_REG_POWER) * _POWER_LSB

    def battery_percent(self) -> float:
        """State of charge 0–100 % based on bus voltage."""
        v   = self.bus_voltage()
        pct = (v - _BATT_V_MIN) / (_BATT_V_MAX - _BATT_V_MIN) * 100.0
        return max(0.0, min(100.0, pct))

    def charging(self) -> bool:
        """True when current is negative (current flowing into battery)."""
        return self.current_ma() < -50   # 50 mA hysteresis

    def read_all(self) -> dict:
        v   = self.bus_voltage()
        i   = self.current_ma()
        p   = self.power_w()
        pct = (v - _BATT_V_MIN) / (_BATT_V_MAX - _BATT_V_MIN) * 100.0
        return {
            "voltage_v":   round(v, 3),
            "current_ma":  round(i, 1),
            "power_w":     round(p, 3),
            "shunt_mv":    round(self.shunt_voltage_mv(), 2),
            "percent":     round(max(0.0, min(100.0, pct)), 1),
            "charging":    i < -50,
            "status":      "Charging" if i < -50 else "Discharging",
        }

    def close(self):
        self._bus.close()


# ------------------------------------------------------------------ poller
# Background thread so camera_stream.py can import a ready-made latest dict

_data_lock   = threading.Lock()
_latest_data = None
_ups         = None
_available   = False


def start(poll_interval: float = 2.0):
    """Start background polling. Safe to call even if UPS is not connected."""
    global _ups, _available

    try:
        _ups       = INA219()
        _available = True
    except Exception as e:
        print(f"[UPS] Not available: {e}")
        return

    def _loop():
        global _latest_data
        while True:
            try:
                data = _ups.read_all()
                with _data_lock:
                    _latest_data = data
            except Exception as e:
                print(f"[UPS] Read error: {e}")
            time.sleep(poll_interval)

    threading.Thread(target=_loop, daemon=True).start()
    print("[UPS] Monitoring started")


def latest() -> dict | None:
    with _data_lock:
        return dict(_latest_data) if _latest_data else None


def is_available() -> bool:
    return _available
