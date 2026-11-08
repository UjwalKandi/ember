#include "sense_wifi.h"

#include <WiFi.h>
#include <esp_wifi.h>
#include <freertos/queue.h>

namespace wifi {
namespace {

struct Rec {
  frames::Mgmt m;
  int8_t rssi;
};

QueueHandle_t queue = nullptr;
portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;
// Exact per-kind counts, kept in the callback so a flood that overflows the queue is still
// counted correctly. Guarded by `mux`.
volatile uint16_t counts[frames::KIND_COUNT];
volatile uint32_t dropped = 0, nCaptured = 0;

features::Extractor ext;
uint8_t plan[MAX_PLAN] = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13};
uint8_t planLen = 13, planIdx = 0;
uint16_t dwell = 300;
uint32_t dwellStart = 0;
uint16_t lastFps[14] = {};

void onFrame(void *buf, wifi_promiscuous_pkt_type_t type) {
  if (type != WIFI_PKT_MGMT) return;
  const wifi_promiscuous_pkt_t *pkt = (const wifi_promiscuous_pkt_t *)buf;
  int len = pkt->rx_ctrl.sig_len - 4;  // sig_len includes the FCS
  Rec r;
  if (len <= 0 || !frames::parseMgmt(pkt->payload, len, r.m)) return;
  r.rssi = pkt->rx_ctrl.rssi;
  portENTER_CRITICAL(&mux);
  if (counts[r.m.kind] < 0xFFFF) counts[r.m.kind]++;
  nCaptured++;
  portEXIT_CRITICAL(&mux);
  if (xQueueSend(queue, &r, 0) != pdTRUE) {
    portENTER_CRITICAL(&mux);
    dropped++;
    portEXIT_CRITICAL(&mux);
  }
}

void drain() {
  Rec r;
  while (xQueueReceive(queue, &r, 0) == pdTRUE) ext.add(r.m, r.rssi, millis());
}

void tune(uint8_t ch) {
  esp_wifi_set_channel(ch, WIFI_SECOND_CHAN_NONE);
  portENTER_CRITICAL(&mux);
  for (auto &c : counts) c = 0;
  portEXIT_CRITICAL(&mux);
  dwellStart = millis();
  ext.begin(ch, dwellStart);
}

}  // namespace

bool begin() {
  queue = xQueueCreate(48, sizeof(Rec));
  if (!queue) return false;
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();  // never associate; we only listen
  wifi_promiscuous_filter_t filt = {.filter_mask = WIFI_PROMIS_FILTER_MASK_MGMT};
  if (esp_wifi_set_promiscuous_filter(&filt) != ESP_OK) return false;
  esp_wifi_set_promiscuous_rx_cb(onFrame);
  if (esp_wifi_set_promiscuous(true) != ESP_OK) return false;
  planIdx = 0;
  tune(plan[0]);
  return true;
}

void setPlan(const uint8_t *channels, uint8_t n, uint16_t dwellMs) {
  uint8_t k = 0;
  for (uint8_t i = 0; i < n && k < MAX_PLAN; i++) {
    if (channels[i] >= 1 && channels[i] <= 13) plan[k++] = channels[i];
  }
  if (k == 0) return;
  planLen = k;
  dwell = constrain(dwellMs, 100, 5000);
  planIdx = 0;
  drain();
  tune(plan[0]);  // the interrupted window is discarded
}

void printPlan() {
  char buf[64];
  size_t o = 0;
  for (uint8_t i = 0; i < planLen && o < sizeof(buf) - 4; i++) {
    o += snprintf(buf + o, sizeof(buf) - o, i ? ",%u" : "%u", plan[i]);
  }
  Serial.printf("LOG|plan %s@%u (cycle %u ms)\n", buf, dwell, (unsigned)planLen * dwell);
}

bool poll(features::Window &out) {
  drain();
  uint32_t now = millis();
  if (now - dwellStart < dwell) return false;
  out = ext.finish(now);
  // replace queue-derived counts with the exact ones from the callback
  portENTER_CRITICAL(&mux);
  for (int k = 0; k < frames::KIND_COUNT; k++) out.cnt[k] = counts[k];
  portEXIT_CRITICAL(&mux);
  uint32_t total = 0;
  for (int k = 0; k < frames::KIND_COUNT; k++) total += out.cnt[k];
  if (out.ch <= 13) lastFps[out.ch] = out.dtMs ? total * 1000 / out.dtMs : 0;
  planIdx = (planIdx + 1) % planLen;
  tune(plan[planIdx]);
  return true;
}

features::Extractor &extractor() { return ext; }
uint16_t fps(uint8_t ch) { return ch <= 13 ? lastFps[ch] : 0; }
uint32_t drops() { return dropped; }
uint32_t captured() { return nCaptured; }

}  // namespace wifi
