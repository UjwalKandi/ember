#include "sense_ble.h"

#include <BLEAdvertisedDevice.h>
#include <BLEDevice.h>
#include <BLEScan.h>
#include <esp_random.h>
#include <freertos/queue.h>
#include <mbedtls/sha256.h>

#include "frames.h"

namespace ble {
namespace {

struct Rec {
  uint8_t addr[6];
  bool rnd;
  int8_t rssi;
  frames::Adv adv;
};

struct Dev {
  bool used;
  Seen s;
  uint32_t lastMs, reportedMs;
};

constexpr int MAX_DEVS = 64;
QueueHandle_t queue = nullptr;
BLEScan *scan = nullptr;
uint8_t salt[16];
Dev devs[MAX_DEVS] = {};
uint32_t lastClear = 0;

class Callbacks : public BLEAdvertisedDeviceCallbacks {
  void onResult(BLEAdvertisedDevice dev) override {
    Rec r;
    if (!frames::parseAdv(dev.getPayload(), dev.getPayloadLength(), r.adv)) return;
    // toString() ("aa:bb:cc:dd:ee:ff") is stable across Bluedroid/NimBLE builds; getNative() isn't
    unsigned int a[6];
    if (sscanf(dev.getAddress().toString().c_str(), "%x:%x:%x:%x:%x:%x", &a[0], &a[1], &a[2], &a[3], &a[4], &a[5]) != 6)
      return;
    for (int i = 0; i < 6; i++) r.addr[i] = a[i];
    r.rnd = (int)dev.getAddressType() != 0;  // BLE_ADDR_TYPE_PUBLIC = 0
    r.rssi = dev.getRSSI();
    xQueueSend(queue, &r, 0);  // a full queue drops; BLE counts are best-effort
  }
};
Callbacks callbacks;

void hashAddr(const uint8_t addr[6], uint8_t out[4]) {
  uint8_t in[22], digest[32];
  memcpy(in, salt, 16);
  memcpy(in + 16, addr, 6);
  mbedtls_sha256(in, sizeof(in), digest, 0);
  memcpy(out, digest, 4);
}

void record(const Rec &r, uint32_t now) {
  uint8_t h[4];
  hashAddr(r.addr, h);
  Dev *slot = nullptr;
  for (auto &d : devs) {
    if (d.used && memcmp(d.s.h, h, 4) == 0) {
      slot = &d;
      break;
    }
  }
  if (!slot) {
    for (auto &d : devs) {
      if (!d.used) {
        slot = &d;
        break;
      }
      if (!slot || (int32_t)(d.lastMs - slot->lastMs) < 0) slot = &d;  // evict least recent
    }
    memset(slot, 0, sizeof(*slot));
    slot->used = true;
    memcpy(slot->s.h, h, 4);
  }
  slot->s.rnd = r.rnd;
  slot->s.rssi = r.rssi;
  if (r.adv.hasMfr) {
    slot->s.hasMfr = true;
    slot->s.mfr = r.adv.mfr;
  }
  if (r.adv.tracker) slot->s.tracker = r.adv.tracker;
  slot->lastMs = now;
}

}  // namespace

bool begin() {
  queue = xQueueCreate(32, sizeof(Rec));
  if (!queue) return false;
  esp_fill_random(salt, sizeof(salt));
  BLEDevice::init("");
  scan = BLEDevice::getScan();
  scan->setAdvertisedDeviceCallbacks(&callbacks, true);  // want duplicates: RSSI keeps updating
  scan->setActiveScan(false);                            // passive: never send scan requests
  scan->setInterval(100);
  scan->setWindow(30);  // ~30% radio time for BLE; the rest stays with Wi-Fi capture
  return scan->start(0, nullptr, false);                 // 0 = scan until stopped, non-blocking
}

void poll() {
  if (!queue) return;
  uint32_t now = millis();
  Rec r;
  while (xQueueReceive(queue, &r, 0) == pdTRUE) record(r, now);
  if (now - lastClear > 10000) {  // the library also keeps results; don't let them grow
    lastClear = now;
    scan->clearResults();
  }
}

bool nextReport(uint32_t nowMs, uint32_t refreshMs, Seen &out) {
  for (auto &d : devs) {
    if (!d.used || !d.s.tracker) continue;
    bool fresh = d.reportedMs == 0;
    bool due = (int32_t)(d.lastMs - d.reportedMs) > 0 && nowMs - d.reportedMs >= refreshMs;
    if (fresh || due) {
      d.reportedMs = nowMs ? nowMs : 1;
      out = d.s;
      return true;
    }
  }
  return false;
}

uint16_t distinct(uint32_t sinceMs) {
  uint16_t n = 0;
  for (const auto &d : devs) n += d.used && (int32_t)(d.lastMs - sinceMs) >= 0;
  return n;
}

uint16_t trackers(uint32_t sinceMs) {
  uint16_t n = 0;
  for (const auto &d : devs) n += d.used && d.s.tracker && (int32_t)(d.lastMs - sinceMs) >= 0;
  return n;
}

}  // namespace ble
