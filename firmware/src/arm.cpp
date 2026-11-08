// Sensor arm (v2): passive Wi-Fi management-frame capture + passive BLE scan, one feature
// window per channel dwell, ELP v2 packets over USB serial (protocol/SPEC.md §4).
//
// R1 transport is USB only: packets are printed as RX|<id>|0|0|<body>, exactly like a v1 node
// bridging itself, so the same bridge, console page and command center read them.
// The arm only listens. It never transmits on Wi-Fi or BLE (no probes, no scan requests).
#include <Arduino.h>
#include <WiFi.h>

#include "config.h"
#include "frames.h"
#include "roles.h"
#include "sense_ble.h"
#include "sense_wifi.h"
#include "settings.h"

namespace arm {
namespace {

// R1 placeholder detectors: fixed per-window thresholds, replaced by the engine's CUSUM (L1)
// and the on-device classifier (L2). The W stream carries the raw features either way.
constexpr uint16_t DEA_MIN = 10;          // deauth+disassoc frames in one dwell
constexpr uint8_t BSP_MIN_NEW = 8;        // never-seen BSSIDs in one dwell
constexpr uint32_t EVENT_HOLDOFF_MS = 5000;
constexpr uint32_t AP_REFRESH_MS = 60000; // re-report each AP while seen (RSSI for localization)
constexpr uint32_t TRK_REFRESH_MS = 30000;
constexpr uint8_t AP_REPORTS_PER_LOOP = 4;

uint8_t selfId = 0;
uint32_t seq = 0;
uint32_t heartbeatS = EMBER_HEARTBEAT_S;
uint32_t lastHeartbeat = 0, lastDea = 0, lastBsp = 0, ledOffAt = 0, warmUntil = 0;
bool wifiOk = false, bleOk = false;
char consoleLine[256];

void send(const char *body) { Serial.printf("RX|%u|0|0|%s\n", selfId, body); }

void hex(char *out, const uint8_t *b, int n) {
  for (int i = 0; i < n; i++) sprintf(out + 2 * i, "%02x", b[i]);
}

// SSID bytes -> printable token: space, '|', '%' and non-printables become %XX.
void escape(char *out, size_t cap, const char *s, uint8_t len) {
  size_t o = 0;
  for (uint8_t i = 0; i < len && o + 4 < cap; i++) {
    uint8_t c = s[i];
    if (c <= 0x20 || c >= 0x7F || c == '%' || c == '|') o += snprintf(out + o, cap - o, "%%%02X", c);
    else out[o++] = c;
  }
  out[o] = '\0';
}

void flash() {
  digitalWrite(LED_PIN, LOW);
  ledOffAt = millis() + 200;
}

void sendWindow(const features::Window &w) {
  using namespace frames;
  char body[EMBER_MAX_BODY + 1];
  snprintf(body, sizeof(body),
           "W|n%u|s%lu|ch%u|dt%u|bcn%u|prq%u|prs%u|ath%u|asc%u|dea%u|dis%u|act%u|bc%u|spf%u|nb%u|nn%u|nss%u"
           "|ds%u|pd%u|sqa%u|tsj%u|iec%u|rx%d|ra%d",
           selfId, (unsigned long)++seq, w.ch, w.dtMs, w.cnt[BEACON], w.cnt[PROBE_REQ], w.cnt[PROBE_RESP],
           w.cnt[AUTH], w.cnt[ASSOC_REQ], w.cnt[DEAUTH], w.cnt[DISASSOC], w.cnt[ACTION], w.bcast, w.spf, w.nb,
           w.nn, w.nss, w.ds, w.pd, w.sqa, w.tsj, w.iec, w.rmax, w.ravg);
  send(body);
}

void detect(const features::Window &w) {
  uint32_t now = millis();
  char body[EMBER_MAX_BODY + 1], b[13];
  uint16_t dea = w.cnt[frames::DEAUTH] + w.cnt[frames::DISASSOC];
  if (dea >= DEA_MIN && now - lastDea > EVENT_HOLDOFF_MS) {
    lastDea = now;
    hex(b, w.deaBssid, 6);
    snprintf(body, sizeof(body), "E|n%u|s%lu|DEA|ch%u|b:%s|cnt%u|rate%u|bc%u|spf%u|r%d", selfId,
             (unsigned long)++seq, w.ch, b, dea, w.dtMs ? dea * 1000u / w.dtMs : 0, w.bcast, w.spf, w.rmax);
    send(body);
    flash();
  }
  // every BSSID is "new" during the first sweeps after boot or a plan change
  if (w.nn >= BSP_MIN_NEW && (int32_t)(now - warmUntil) > 0 && now - lastBsp > EVENT_HOLDOFF_MS) {
    lastBsp = now;
    snprintf(body, sizeof(body), "E|n%u|s%lu|BSP|ch%u|nn%u|nb%u", selfId, (unsigned long)++seq, w.ch, w.nn, w.nb);
    send(body);
    flash();
  }
}

void sendAp(const features::Ap &ap) {
  char body[EMBER_MAX_BODY + 1], b[13], ss[100];
  hex(b, ap.bssid, 6);
  escape(ss, sizeof(ss), ap.ssid, ap.hidden ? 0 : ap.ssidLen);
  snprintf(body, sizeof(body), "A|n%u|s%lu|b:%s|ss:%s|ch%u|sec:%s|pmf%u|r%d|bi%u|ie:%04x%s", selfId,
           (unsigned long)++seq, b, ss, ap.ch, frames::secName(ap.sec), ap.pmf, ap.rssi, ap.bi, ap.ie,
           ap.hidden ? "|hid1" : "");
  send(body);
}

void sendTracker(const ble::Seen &s) {
  char body[EMBER_MAX_BODY + 1], h[9], mf[12] = "";
  hex(h, s.h, 4);
  if (s.hasMfr) snprintf(mf, sizeof(mf), "|mf:%04X", s.mfr);
  snprintf(body, sizeof(body), "B|n%u|s%lu|h:%s|trk%u%s|rnd%u|r%d", selfId, (unsigned long)++seq, h, s.tracker, mf,
           s.rnd, s.rssi);
  send(body);
}

void sendHeartbeat(uint32_t sinceMs) {
  char body[EMBER_MAX_BODY + 1], occ[100] = "";
  size_t o = 0;
  for (uint8_t ch = 1; ch <= 13; ch++) {
    uint16_t f = wifi::fps(ch);
    if (f) o += snprintf(occ + o, sizeof(occ) - o, o ? ",%u=%u" : "%u=%u", ch, f);
  }
  snprintf(body, sizeof(body), "H|n%u|s%lu|up%lu|ap%d|ble%u|trk%u|drp%lu|occ:%s", selfId, (unsigned long)++seq,
           (unsigned long)(millis() / 1000), wifi::extractor().apCount(), bleOk ? ble::distinct(sinceMs) : 0,
           bleOk ? ble::trackers(sinceMs) : 0, (unsigned long)wifi::drops(), occ);
  send(body);
}

void runSelftest() {
  Serial.printf("LOG|selftest fw=%s role=arm id=%u chip=%s rev%d mac=%s heap=%u\n", EMBER_FW, selfId,
                ESP.getChipModel(), ESP.getChipRevision(), WiFi.macAddress().c_str(), ESP.getFreeHeap());
  Serial.printf("LOG|selftest wifi=%s frames=%lu aps=%d drops=%lu\n", wifiOk ? "ok" : "FAIL",
                (unsigned long)wifi::captured(), wifi::extractor().apCount(), (unsigned long)wifi::drops());
  if (wifiOk && wifi::captured() == 0) Serial.println("LOG|selftest warn: no frames yet (is the antenna attached?)");
  Serial.printf("LOG|selftest ble=%s advertisers_60s=%u\n", bleOk ? "ok" : "FAIL",
                bleOk ? ble::distinct(millis() - 60000) : 0);
  wifi::printPlan();
  if (wifiOk && bleOk) Serial.println("LOG|selftest PASS");
  else Serial.printf("LOG|selftest FAIL: %s%s\n", wifiOk ? "" : "wifi ", bleOk ? "" : "ble");
}

// PLAN|1,6,11@300 -> channels and dwell (ms). RAM only.
void setPlan(const char *spec) {
  uint8_t chans[wifi::MAX_PLAN], n = 0;
  int dwell = 300;
  const char *p = spec;
  while (*p && *p != '@' && n < wifi::MAX_PLAN) {
    int c = atoi(p);
    if (c >= 1 && c <= 13) chans[n++] = c;
    while (*p && *p != ',' && *p != '@') p++;
    if (*p == ',') p++;
  }
  if (*p == '@') dwell = atoi(p + 1);
  if (n == 0) {
    Serial.println("LOG|plan error: use PLAN|1,6,11@300 (channels 1-13, dwell 100-5000 ms)");
    return;
  }
  wifi::setPlan(chans, n, dwell);
  warmUntil = millis() + 2 * (uint32_t)n * constrain(dwell, 100, 5000);
  wifi::printPlan();
}

void handleConsoleLine(const char *l) {
  if (strncmp(l, "PLAN|", 5) == 0) setPlan(l + 5);
  else if (strcmp(l, "PLAN?") == 0) wifi::printPlan();
  else Serial.printf("LOG|ignored line: %s (arm commands: PLAN?, PLAN|1,6,11@300)\n", l);
}

}  // namespace

void setup() {
  Serial.begin(115200);
  Serial.setTxTimeoutMs(0);  // never block when no host is listening
  uint32_t t0 = millis();
  while (!Serial && millis() - t0 < 1500) delay(10);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, HIGH);
  console::begin(runSelftest);

  selfId = settings::nodeId();
  if (selfId == 0) {
    Serial.printf("LOG|EMBER %s role=arm UNPROVISIONED: send CFG|id=<1-254>\n", EMBER_FW);
    return;  // stay idle (radio off) until provisioned
  }
  wifiOk = wifi::begin();
  bleOk = ble::begin();
  warmUntil = millis() + 2 * 13 * 300;
  Serial.printf("LOG|EMBER %s role=arm id=%u mac=%s wifi=%s ble=%s (listen-only)\n", EMBER_FW, selfId,
                WiFi.macAddress().c_str(), wifiOk ? "ok" : "FAIL", bleOk ? "ok" : "FAIL");
  wifi::printPlan();
  lastHeartbeat = millis() - heartbeatS * 1000;  // heartbeat right away
}

void loop() {
  uint32_t now = millis();
  if (selfId == 0) {
    static uint32_t lastNag = 0;
    console::poll(consoleLine, sizeof(consoleLine));  // only CFG/TEST/REBOOT do anything here
    if (now - lastNag > 5000) {
      lastNag = now;
      Serial.println("LOG|unprovisioned: send CFG|id=<1-254>");
    }
    return;
  }
  if (console::poll(consoleLine, sizeof(consoleLine))) handleConsoleLine(consoleLine);

  features::Window w;
  if (wifiOk && wifi::poll(w)) {
    sendWindow(w);
    detect(w);
  }
  for (uint8_t i = 0; i < AP_REPORTS_PER_LOOP; i++) {
    const features::Ap *ap = wifi::extractor().nextReport(now, AP_REFRESH_MS);
    if (!ap) break;
    sendAp(*ap);
  }
  if (bleOk) {
    ble::poll();
    ble::Seen s;
    while (ble::nextReport(now, TRK_REFRESH_MS, s)) sendTracker(s);
  }
  if (now - lastHeartbeat >= heartbeatS * 1000) {
    sendHeartbeat(lastHeartbeat);
    lastHeartbeat = now;
  }
  if (ledOffAt && (int32_t)(now - ledOffAt) >= 0) {
    ledOffAt = 0;
    digitalWrite(LED_PIN, HIGH);
  }
}

}  // namespace arm
