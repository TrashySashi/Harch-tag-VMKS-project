/*
 * HArch Tag — vest firmware (ESP32-WROOM-32)
 *
 * Hit detection: a TSOP38238 38 kHz IR receiver on TSOP_PIN. Its OUT line is
 * active-LOW — it pulls the pin to GND while it sees modulated 38 kHz IR (a
 * "shot"). We report one hit per falling edge (HIGH -> LOW), with a 300 ms
 * debounce that mirrors the score server so one shot counts once.
 *
 * Wiring (TSOP38238, dome facing you, pins left-to-right): 1=OUT->TSOP_PIN,
 * 2=GND->GND, 3=VCC->3V3. Datasheet noise filter: ~100 ohm in series on VCC +
 * ~4.7 uF across VCC-GND at the sensor.
 *
 * On a "hit" it sends:  POST http://<server>:5001/hit
 *
 * Note: Wokwi has no TSOP part — use a pushbutton to GND as a stand-in (it
 * pulls the pin LOW the same way). The hit logic is identical either way.
 * Wokwi runs in the cloud, so it CANNOT reach your laptop's localhost. Point
 * SERVER_HOST at a public URL (e.g. an ngrok tunnel to your score server), or
 * flash this to a real ESP32 on the same Wi-Fi as the Pi and use the Pi's IP.
 */

#include <WiFi.h>
#include <HTTPClient.h>

// Credentials live in secrets.h (git-ignored). Copy secrets.example.h to
// secrets.h and fill in your values. In Wokwi, add a secrets.h file.
#include "secrets.h"

const int TSOP_PIN = 4;                   // TSOP38238 OUT (active-LOW on 38 kHz IR)
const unsigned long DEBOUNCE_MS = 300;    // one shot = one hit

unsigned long lastHitMs = 0;
int lastTsopState = HIGH;                 // idle (no IR) is HIGH

// --- Test emitter (one TSAL6200 via 220 ohm on GPIO 17) -----------------
// A TSOP only sees IR modulated at 38 kHz, so the "blink" is a 38 kHz carrier
// gated on/off: a short burst every 3 s. LEDC makes the carrier in hardware;
// millis() gates it without blocking the receiver edge-detection in loop().
const int EMITTER_PIN = 17;
const int EMITTER_FREQ_HZ = 38000;        // TSOP38238 carrier
const unsigned long EMIT_PERIOD_MS = 3000; // "blink" once every 3 s
const unsigned long EMIT_BURST_MS = 200;   // carrier on for 200 ms per blink

unsigned long lastEmitMs = 0;
bool emitting = false;

void carrierOn()  { ledcWrite(EMITTER_PIN, 128); }  // ~50% duty = carrier
void carrierOff() { ledcWrite(EMITTER_PIN, 0); }

void setup() {
  Serial.begin(115200);
  pinMode(TSOP_PIN, INPUT);               // TSOP drives the line itself

  // 38 kHz PWM carrier on the emitter pin, 8-bit duty resolution.
  ledcAttach(EMITTER_PIN, EMITTER_FREQ_HZ, 8);
  carrierOff();

  Serial.print("Connecting to Wi-Fi");
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) {
    delay(250);
    Serial.print(".");
  }
  Serial.println();
  Serial.print("Connected, IP: ");
  Serial.println(WiFi.localIP());
}

void reportHit() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("HIT but Wi-Fi down — not sent");
    return;
  }
  HTTPClient http;
  String url = "http://" + String(SERVER_HOST) + ":" + String(SERVER_PORT) + "/hit";
  http.begin(url);
  int code = http.POST("");               // body unused; the route just counts
  Serial.print("POST /hit -> ");
  Serial.print(code);
  Serial.print("  ");
  Serial.println(http.getString());
  http.end();
}

void loop() {
  // Test emitter: gate the 38 kHz carrier into a 200 ms burst every 3 s.
  unsigned long now = millis();
  if (!emitting && now - lastEmitMs >= EMIT_PERIOD_MS) {
    emitting = true;
    lastEmitMs = now;
    carrierOn();
  } else if (emitting && now - lastEmitMs >= EMIT_BURST_MS) {
    emitting = false;
    carrierOff();
  }

  int tsopState = digitalRead(TSOP_PIN);

  // Falling edge (HIGH -> LOW) = the TSOP just started seeing a 38 kHz shot.
  if (tsopState == LOW && lastTsopState == HIGH) {
    if (now - lastHitMs >= DEBOUNCE_MS) {
      lastHitMs = now;
      Serial.println("HIT detected");
      reportHit();
    }
  }
  lastTsopState = tsopState;
}
