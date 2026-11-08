#include "features.h"

#include <string.h>

namespace features {

using frames::Mgmt;

void Extractor::Distinct::add(uint32_t v) {
  for (uint8_t i = 0; i < n; i++) {
    if (h[i] == v) return;
  }
  if (n < MAX_DISTINCT) h[n++] = v;  // saturates: the count is a lower bound beyond 48
}

void Extractor::begin(uint8_t ch, uint32_t nowMs) {
  memset(&w_, 0, sizeof(w_));
  w_.ch = ch;
  startMs_ = nowMs;
  rssiSum_ = 0;
  rssiN_ = 0;
  bssids_.clear();
  ssids_.clear();
  deaSrc_.clear();
  probers_.clear();
}

Window Extractor::finish(uint32_t nowMs) {
  w_.dtMs = (uint16_t)(nowMs - startMs_);
  w_.nb = bssids_.n;
  w_.nss = ssids_.n;
  w_.ds = deaSrc_.n;
  w_.pd = probers_.n;
  w_.rmax = rssiN_ ? w_.rmax : -127;
  w_.ravg = rssiN_ ? (int8_t)(rssiSum_ / rssiN_) : -127;
  return w_;
}

Ap *Extractor::find(const uint8_t bssid[6]) {
  for (auto &ap : aps_) {
    if (ap.used && memcmp(ap.bssid, bssid, 6) == 0) return &ap;
  }
  return nullptr;
}

Ap *Extractor::insert(const uint8_t bssid[6], uint32_t nowMs) {
  Ap *slot = nullptr;
  for (auto &ap : aps_) {
    if (!ap.used) {
      slot = &ap;
      break;
    }
    if (!slot || (int32_t)(ap.lastMs - slot->lastMs) < 0) slot = &ap;  // least recently seen
  }
  memset(slot, 0, sizeof(*slot));
  slot->used = true;
  memcpy(slot->bssid, bssid, 6);
  slot->dirty = true;
  slot->lastMs = nowMs;
  return slot;
}

void Extractor::updateAp(Ap &ap, const Mgmt &m, int8_t rssi, uint32_t nowMs, bool fresh) {
  bool beacon = m.kind == frames::BEACON;
  uint8_t ch = m.channel ? m.channel : w_.ch;
  // Probe responses answer the probe's SSID and may omit hidden-network details; the beacon
  // is authoritative for SSID and IE fingerprint.
  if (beacon || fresh) {
    if (!m.hidden && (ap.ssidLen != m.ssidLen || memcmp(ap.ssid, m.ssid, m.ssidLen) != 0)) {
      memcpy(ap.ssid, m.ssid, m.ssidLen + 1);
      ap.ssidLen = m.ssidLen;
      ap.dirty = true;
    }
    if (ap.hidden != m.hidden) {
      ap.hidden = m.hidden;
      ap.dirty = true;
    }
  }
  if (ap.ch != ch || ap.sec != m.sec || ap.pmf != m.pmf || ap.bi != m.beaconInt) {
    ap.ch = ch;
    ap.sec = m.sec;
    ap.pmf = m.pmf;
    ap.bi = m.beaconInt;
    ap.dirty = true;
  }
  if (beacon) {
    if (ap.ie && ap.ie != m.ieHash && w_.iec < 255) w_.iec++;
    if (ap.ie != m.ieHash) ap.dirty = true;
    ap.ie = m.ieHash;
    if (!fresh && ap.tsfMs && nowMs - ap.tsfMs < SAME_DWELL_MS && m.tsf < ap.lastTsf && w_.tsj < 255) w_.tsj++;
    ap.lastTsf = m.tsf;
    ap.tsfMs = nowMs;
  }
  ap.rssi = rssi;
}

void Extractor::add(const Mgmt &m, int8_t rssi, uint32_t nowMs) {
  if (m.kind >= frames::KIND_COUNT) return;
  if (w_.cnt[m.kind] < 0xFFFF) w_.cnt[m.kind]++;
  if (!rssiN_ || rssi > w_.rmax) w_.rmax = rssi;
  rssiSum_ += rssi;
  rssiN_++;

  // Sequence continuity for frames sent by a known AP (its own address as transmitter).
  Ap *ap = find(m.bssid);
  bool fromAp = ap && memcmp(m.sa, m.bssid, 6) == 0;
  if (fromAp) {
    uint16_t gap = (m.seq - ap->lastSeq) & 0x0FFF;
    if (nowMs - ap->lastMs < SAME_DWELL_MS && !m.retry && (gap == 0 || gap > SEQ_GAP) && w_.sqa < 255) w_.sqa++;
    ap->lastSeq = m.seq;
  }

  switch (m.kind) {
    case frames::BEACON:
    case frames::PROBE_RESP: {
      bool fresh = ap == nullptr;
      if (fresh) {
        ap = insert(m.bssid, nowMs);
        ap->lastSeq = m.seq;
        if (w_.nn < 255) w_.nn++;
      }
      updateAp(*ap, m, rssi, nowMs, fresh);
      ap->lastMs = nowMs;
      if (m.kind == frames::BEACON) {
        bssids_.add(frames::macHash(m.bssid));
        if (!m.hidden) {
          uint32_t h = 2166136261u;
          for (uint8_t i = 0; i < m.ssidLen; i++) h = (h ^ (uint8_t)m.ssid[i]) * 16777619u;
          ssids_.add(h);
        }
      }
      break;
    }
    case frames::PROBE_REQ:
      probers_.add(frames::macHash(m.sa));
      break;
    case frames::DEAUTH:
    case frames::DISASSOC:
      deaSrc_.add(frames::macHash(m.sa));
      if (frames::isBroadcast(m.da) && w_.bcast < 0xFFFF) w_.bcast++;
      if (!m.prot && ap && ap->pmf == 2 && w_.spf < 0xFFFF) w_.spf++;
      memcpy(w_.deaBssid, m.bssid, 6);
      if (ap) ap->lastMs = nowMs;
      break;
    default:
      if (fromAp) ap->lastMs = nowMs;
      break;
  }
}

const Ap *Extractor::nextReport(uint32_t nowMs, uint32_t refreshMs) {
  for (auto &ap : aps_) {
    if (!ap.used) continue;
    bool stale = (int32_t)(ap.lastMs - ap.reportedMs) > 0 && nowMs - ap.reportedMs >= refreshMs;
    if (ap.dirty || stale) {
      ap.dirty = false;
      ap.reportedMs = nowMs;
      return &ap;
    }
  }
  return nullptr;
}

int Extractor::apCount() const {
  int n = 0;
  for (const auto &ap : aps_) n += ap.used;
  return n;
}

}  // namespace features
