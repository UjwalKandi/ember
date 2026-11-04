#include "mesh.h"

#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>

#include "config.h"

namespace mesh {
namespace {

const uint8_t BROADCAST[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

struct RawRx {
  uint8_t len;
  int8_t rssi;
  uint8_t data[HEADER_LEN + MAX_BODY];
};

struct DedupeEntry {
  uint16_t pktId;
  uint8_t src;
  bool used;
};

struct PendingRelay {
  bool used;
  bool retried;  // one retry if the Wi-Fi driver was out of buffers
  uint32_t dueMs;
  uint8_t len;
  uint8_t data[HEADER_LEN + MAX_BODY];
};

constexpr int DEDUPE_SIZE = 32;
constexpr int RELAY_SLOTS = 8;

QueueHandle_t rxQueue = nullptr;
RxHandler rxHandler = nullptr;
uint8_t self = 0;
uint16_t nextPktId = 0;

DedupeEntry dedupe[DEDUPE_SIZE];
int dedupeHead = 0;
PendingRelay relays[RELAY_SLOTS];

bool seen(uint8_t src, uint16_t id) {
  for (const auto &e : dedupe) {
    if (e.used && e.src == src && e.pktId == id) return true;
  }
  return false;
}

void remember(uint8_t src, uint16_t id) {
  dedupe[dedupeHead] = {id, src, true};
  dedupeHead = (dedupeHead + 1) % DEDUPE_SIZE;
}

// Runs in the Wi-Fi task: copy and hand off, never send from here.
void onRecv(const esp_now_recv_info_t *info, const uint8_t *data, int len) {
  if (len < HEADER_LEN || len > HEADER_LEN + MAX_BODY) return;
  if (data[0] != MAGIC || data[1] != VERSION) return;
  RawRx rx;
  rx.len = len;
  rx.rssi = (info && info->rx_ctrl) ? info->rx_ctrl->rssi : 0;
  memcpy(rx.data, data, len);
  xQueueSend(rxQueue, &rx, 0);
}

void scheduleRelay(const uint8_t *data, uint8_t len) {
  for (auto &r : relays) {
    if (r.used) continue;
    r.used = true;
    r.retried = false;
    r.len = len;
    memcpy(r.data, data, len);
    r.data[5] -= 1;  // ttl
    r.data[6] += 1;  // hops
    r.dueMs = millis() + random(5, 41);  // jitter so neighbours don't collide
    return;
  }
  // All slots busy: drop the relay. Other nodes will usually cover it.
}

void handleRx(const RawRx &rx) {
  Frame f;
  f.pktId = rx.data[2] | (rx.data[3] << 8);
  f.src = rx.data[4];
  f.ttl = rx.data[5];
  f.hops = rx.data[6];
  f.len = rx.data[7];
  f.rssi = rx.rssi;
  if (f.len > MAX_BODY || HEADER_LEN + f.len > rx.len) return;
  if (f.src == self || seen(f.src, f.pktId)) return;
  remember(f.src, f.pktId);

  memcpy(f.body, rx.data + HEADER_LEN, f.len);
  f.body[f.len] = '\0';

  // The gateway only floods its own TX; nodes don't relay commands addressed to themselves.
  char mine[12];
  snprintf(mine, sizeof(mine), "C|n%u|", self);
  bool forMe = strncmp(f.body, mine, strlen(mine)) == 0;
  if (f.ttl > 0 && self != 0 && !forMe) scheduleRelay(rx.data, HEADER_LEN + f.len);
  if (rxHandler) rxHandler(f);
}

}  // namespace

bool begin(uint8_t selfId, RxHandler handler) {
  self = selfId;
  rxHandler = handler;
  // Random start so a rebooted node isn't mistaken for duplicates of its old packets.
  nextPktId = esp_random() & 0xFFFF;
  rxQueue = xQueueCreate(16, sizeof(RawRx));

  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  // Enable Espressif Long Range (LR) mode alongside b/g/n. LR frames are only
  // decodable by other ESP32s, which is all this mesh contains; b/g/n stays on
  // so standard-rate frames still work.
  esp_wifi_set_protocol(WIFI_IF_STA, WIFI_PROTOCOL_11B | WIFI_PROTOCOL_11G |
                                         WIFI_PROTOCOL_11N | WIFI_PROTOCOL_LR);
  esp_wifi_set_channel(EMBER_CHANNEL, WIFI_SECOND_CHAN_NONE);

  if (esp_now_init() != ESP_OK) return false;
  esp_now_peer_info_t peer = {};
  memcpy(peer.peer_addr, BROADCAST, 6);
  peer.channel = EMBER_CHANNEL;
  peer.ifidx = WIFI_IF_STA;
  peer.encrypt = false;
  if (esp_now_add_peer(&peer) != ESP_OK) return false;
  return esp_now_register_recv_cb(onRecv) == ESP_OK;
}

bool send(const char *body) {
  size_t len = strlen(body);
  if (len == 0 || len > MAX_BODY) return false;
  uint8_t buf[HEADER_LEN + MAX_BODY];
  uint16_t id = nextPktId++;
  buf[0] = MAGIC;
  buf[1] = VERSION;
  buf[2] = id & 0xFF;
  buf[3] = id >> 8;
  buf[4] = self;
  buf[5] = EMBER_DEFAULT_TTL;
  buf[6] = 0;
  buf[7] = len;
  memcpy(buf + HEADER_LEN, body, len);
  remember(self, id);
  esp_err_t err = esp_now_send(BROADCAST, buf, HEADER_LEN + len);
  if (err == ESP_ERR_ESPNOW_NO_MEM) {  // TX buffers full: give the driver a moment, retry once
    delay(5);
    err = esp_now_send(BROADCAST, buf, HEADER_LEN + len);
  }
  return err == ESP_OK;
}

void loop() {
  RawRx rx;
  while (xQueueReceive(rxQueue, &rx, 0) == pdTRUE) handleRx(rx);

  uint32_t now = millis();
  for (auto &r : relays) {
    if (r.used && (int32_t)(now - r.dueMs) >= 0) {
      esp_err_t err = esp_now_send(BROADCAST, r.data, r.len);
      if (err == ESP_ERR_ESPNOW_NO_MEM && !r.retried) {
        r.retried = true;  // keep the slot, try again on the next loop()
        continue;
      }
      r.used = false;
    }
  }
}

}  // namespace mesh
