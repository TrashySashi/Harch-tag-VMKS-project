/*
 * Template for vest credentials. Copy this file to "secrets.h" and fill in
 * your values. secrets.h is git-ignored, so your real Wi-Fi password never
 * gets committed.
 *
 *     cp secrets.example.h secrets.h    (then edit secrets.h)
 *
 * In Wokwi, add a file named "secrets.h" to the project with these defines.
 */
#ifndef SECRETS_H
#define SECRETS_H

#define WIFI_SSID   "Wokwi-GUEST"   // your Wi-Fi name (Wokwi test net needs no pass)
#define WIFI_PASS   ""              // your Wi-Fi password
#define SERVER_HOST "127.0.0.1"     // Pi IP, or ngrok host for Wokwi
#define SERVER_PORT 5001

#endif  // SECRETS_H
