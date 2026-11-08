#pragma once
#include <Arduino.h>

// Passive BLE advertisement scan (no scan requests are sent). Addresses never leave the arm:
// each is reduced to trunc(SHA-256(salt || address), 4 B). The salt is random per boot
// (a daily salt shared across arms belongs to the deferred multi-arm design).
namespace ble {

struct Seen {
  uint8_t h[4];     // salted address hash
  bool rnd;         // random (vs public) address
  bool hasMfr;
  uint16_t mfr;
  uint8_t tracker;  // frames::Tracker
  int8_t rssi;
};

bool begin();
void poll();  // drain the scan callback queue; call from loop()

// Next known tracker due a B packet (first sight, then every refreshMs while still seen).
bool nextReport(uint32_t nowMs, uint32_t refreshMs, Seen &out);
uint16_t distinct(uint32_t sinceMs);  // advertisers seen since sinceMs
uint16_t trackers(uint32_t sinceMs);  // of which known trackers

}  // namespace ble
