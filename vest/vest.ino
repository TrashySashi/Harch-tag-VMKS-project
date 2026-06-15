/*
 * HArch Tag — vest firmware (ESP32-WROOM-32)
 *
 * Simulation stand-in: a pushbutton replaces the TSOP IR receiver, exactly as
 * planned in DEVELOPMENT_NOTES.md. The hit-handling logic (debounce + report)
 * is identical to the real hardware; only the input line changes later.
 *
 * On a "hit" it sends:  POST http://<server>:5001/hit
 *
 * Test in Wokwi (https://wokwi.com/esp32): default board is the WROOM-32.
 * Wokwi runs in the cloud, so it CANNOT reach your laptop's localhost. Point
 * SERVER_HOST at a public URL (e.g. an ngrok tunnel to your score server), or
 * flash this to a real ESP32 on the same Wi-Fi as the Pi and use the Pi's IP.
 */

#include <WiFi.h>
#include <HTTPClient.h>

// Credentials live in secrets.h (git-ignored). Copy secrets.example.h to
// secrets.h and fill in your values. In Wokwi, add a secrets.h file.
#include "secrets.h"

const int BUTTON_PIN = 4;                 // safe GPIO; button to GND
const unsigned long DEBOUNCE_MS = 300;    // one shot = one hit

unsigned long lastHitMs = 0;
int lastButtonState = HIGH;               // INPUT_PULLUP => released is HIGH

void setup() {
  Serial.begin(115200);
  pinMode(BUTTON_PIN, INPUT_PULLUP);

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
  // Temporary: auto-fire a hit every 2 seconds to exercise the scoring server.
  // (Replace with the button/TSOP edge detection once hardware is wired.)
  Serial.println("HIT detected");
  reportHit();
  delay(2000);
}
