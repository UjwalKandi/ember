#pragma once
#include <Arduino.h>

// Meshtastic-style managed flooding over ESP-NOW broadcast. See protocol/SPEC.md §1.
namespace mesh {

constexpr uint8_t MAGIC = 0xE7;
constexpr uint8_t VERSION = 1;
constexpr uint8_t HEADER_LEN = 8;
constexpr uint8_t MAX_BODY = 200;

struct Frame {
  uint16_t pktId;
  uint8_t src;
  uint8_t ttl;
  uint8_t hops;
  int8_t rssi;
  uint8_t len;
  char body[MAX_BODY + 1];  // NUL-terminated
};

typedef void (*RxHandler)(const Frame &frame);

// selfId: 0 for the gateway, 1-254 for nodes. The handler is called from loop()
// once per unique frame (never for our own frames).
bool begin(uint8_t selfId, RxHandler handler);

// Originate a frame with our id. Returns false if the body is too long or the send failed.
bool send(const char *body);

// Drain received frames, deliver new ones, and send relays that are due.
// Call often (at least every ~20 ms) to keep relay jitter accurate.
void loop();

}  // namespace mesh
