// USB gateway: bridges the ESP-NOW mesh to the laptop over serial (protocol/SPEC.md §3).
#ifdef EMBER_ROLE_GATEWAY

#include <Arduino.h>
#include <WiFi.h>

#include "config.h"
#include "mesh.h"
#include "roles.h"

namespace gateway {
namespace {

char line[256];
size_t lineLen = 0;

void onFrame(const mesh::Frame &f) {
  Serial.printf("RX|%u|%u|%d|%s\n", f.src, f.hops, f.rssi, f.body);
}

void handleLine(const char *l) {
  if (strncmp(l, "TX|", 3) != 0) {
    Serial.printf("LOG|ignored line: %s\n", l);
    return;
  }
  const char *body = l + 3;
  if (!mesh::send(body)) Serial.printf("LOG|tx failed (%u bytes)\n", (unsigned)strlen(body));
}

}  // namespace

void setup() {
  Serial.begin(115200);
  uint32_t t0 = millis();
  while (!Serial && millis() - t0 < 2000) delay(10);

  bool ok = mesh::begin(0, onFrame);
  Serial.printf("LOG|EMBER gateway v1 mac=%s ch=%d mesh=%s\n", WiFi.macAddress().c_str(),
                EMBER_CHANNEL, ok ? "ok" : "FAIL");
}

void loop() {
  mesh::loop();
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      line[lineLen] = '\0';
      if (lineLen > 0) handleLine(line);
      lineLen = 0;
    } else if (lineLen < sizeof(line) - 1) {
      line[lineLen++] = c;
    }
  }
}

}  // namespace gateway

#endif  // EMBER_ROLE_GATEWAY
