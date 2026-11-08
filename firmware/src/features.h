#pragma once
#include <stdint.h>

#include "frames.h"

// Feature windows and the AP table, fed with parsed management frames. Pure C++ (host-tested).
//
// One window = one dwell on one channel. Its fields are the W packet (protocol/SPEC.md) and
// the input vector of the L2 on-device classifier, so changing them is a protocol change.
namespace features {

struct Window {
  uint8_t ch;
  uint16_t dtMs;
  uint16_t cnt[frames::KIND_COUNT];  // frames by kind
  uint16_t bcast;  // deauth/disassoc to the broadcast address
  uint16_t spf;    // unprotected deauth/disassoc claiming to come from a PMF-required AP
  uint8_t nb;      // distinct BSSIDs beaconing
  uint8_t nn;      // BSSIDs never seen before (AP table misses)
  uint8_t nss;     // distinct SSIDs beaconed
  uint8_t ds;      // distinct deauth/disassoc source addresses
  uint8_t pd;      // distinct probe-request sources
  uint8_t sqa;     // sequence-number anomalies on known BSSIDs (gap or repeat within a dwell)
  uint8_t tsj;     // beacon TSF went backwards on a known BSSID (two radios, one BSSID)
  uint8_t iec;     // beacon IE fingerprint changed on a known BSSID
  int8_t rmax;     // strongest RSSI (-127 if no frames)
  int8_t ravg;     // mean RSSI (-127 if no frames)
  uint8_t deaBssid[6];  // BSSID of the last deauth/disassoc (for the DEA event)
};

struct Ap {
  bool used;
  uint8_t bssid[6];
  char ssid[33];
  uint8_t ssidLen;
  bool hidden;
  uint8_t ch;
  frames::Sec sec;
  uint8_t pmf;
  uint16_t bi;
  uint16_t ie;     // beacon IE fingerprint, 0 until a beacon is seen
  int8_t rssi;
  uint16_t lastSeq;
  uint64_t lastTsf;
  uint32_t lastMs, reportedMs, tsfMs;
  bool dirty;      // a reported field changed since the last A packet
};

constexpr int MAX_APS = 64;
constexpr int MAX_DISTINCT = 48;
constexpr uint32_t SAME_DWELL_MS = 1000;  // seq/TSF continuity is only judged within this gap
constexpr uint16_t SEQ_GAP = 512;

class Extractor {
 public:
  void begin(uint8_t ch, uint32_t nowMs);  // start a window
  void add(const frames::Mgmt &m, int8_t rssi, uint32_t nowMs);
  Window finish(uint32_t nowMs);           // close the window (call begin for the next)

  // Next AP due an A packet: changed, or seen since its last report and older than refreshMs.
  // Marks it reported. Returns nullptr when nothing is due.
  const Ap *nextReport(uint32_t nowMs, uint32_t refreshMs);
  int apCount() const;

 private:
  struct Distinct {
    uint32_t h[MAX_DISTINCT];
    uint8_t n;
    void clear() { n = 0; }
    void add(uint32_t v);
  };
  Ap *find(const uint8_t bssid[6]);
  Ap *insert(const uint8_t bssid[6], uint32_t nowMs);
  void updateAp(Ap &ap, const frames::Mgmt &m, int8_t rssi, uint32_t nowMs, bool fresh);

  Ap aps_[MAX_APS] = {};
  Window w_ = {};
  uint32_t startMs_ = 0;
  int32_t rssiSum_ = 0;
  uint16_t rssiN_ = 0;
  Distinct bssids_ = {}, ssids_ = {}, deaSrc_ = {}, probers_ = {};
};

}  // namespace features
