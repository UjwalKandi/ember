// USB gateway: bridges the ESP-NOW mesh to the laptop over serial (protocol/SPEC.md §3).
#include <Arduino.h>
#include <WiFi.h>

#include "config.h"
#include "mesh.h"
#include "roles.h"
#include "selftest.h"
#include "settings.h"

namespace gateway {
namespace {

char line[256];
bool meshOk = false, camOk = false, micOk = false;

void onFrame(const mesh::Frame &f) {
  Serial.printf("RX|%u|%u|%d|%s\n", f.src, f.hops, f.rssi, f.body);
}

void runSelftest() { selftest::run(meshOk, camOk, micOk, false); }

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
  Serial.setTxTimeoutMs(0);  // never block when no host is listening
  uint32_t t0 = millis();
  while (!Serial && millis() - t0 < 2000) delay(10);

  meshOk = mesh::begin(0, onFrame);
  console::begin(runSelftest);
  Serial.printf("LOG|EMBER %s role=gateway mac=%s ch=%d mesh=%s\n", EMBER_FW, WiFi.macAddress().c_str(),
                EMBER_CHANNEL, meshOk ? "ok" : "FAIL");
}

void loop() {
  mesh::loop();
  if (console::poll(line, sizeof(line))) handleLine(line);
}

}  // namespace gateway
