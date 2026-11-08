#include "frames.h"

#include <string.h>

namespace frames {
namespace {

uint16_t le16(const uint8_t *p) { return p[0] | (p[1] << 8); }

uint64_t le64(const uint8_t *p) {
  uint64_t v = 0;
  for (int i = 7; i >= 0; i--) v = (v << 8) | p[i];
  return v;
}

constexpr uint32_t FNV_OFFSET = 2166136261u, FNV_PRIME = 16777619u;

uint32_t fnv(uint32_t h, uint8_t b) { return (h ^ b) * FNV_PRIME; }

// RSN IE body (after id/len): version, group cipher, pairwise list, AKM list, capabilities.
void parseRsn(const uint8_t *p, size_t n, Mgmt &m) {
  m.sec = SEC_WPA2;  // RSN with an unknown AKM still means WPA2-class
  if (n < 8) return;
  size_t i = 2 + 4;  // version + group cipher
  uint16_t pc = le16(p + i);
  i += 2 + 4 * (size_t)pc;
  if (i + 2 > n) return;
  uint16_t ac = le16(p + i);
  i += 2;
  bool psk = false, sae = false, dot1x = false, owe = false;
  for (uint16_t k = 0; k < ac && i + 4 <= n; k++, i += 4) {
    if (!(p[i] == 0x00 && p[i + 1] == 0x0F && p[i + 2] == 0xAC)) continue;  // IEEE OUI only
    switch (p[i + 3]) {
      case 2: case 6: psk = true; break;
      case 8: case 24: sae = true; break;
      case 18: owe = true; break;
      case 1: case 3: case 5: case 11: case 12: case 13: dot1x = true; break;
    }
  }
  if (sae && psk) m.sec = SEC_WPA23;
  else if (sae) m.sec = SEC_WPA3;
  else if (owe) m.sec = SEC_OWE;
  else if (dot1x) m.sec = SEC_ENT;
  if (i + 2 <= n) {
    uint16_t caps = le16(p + i);
    m.pmf = (caps & 0x40) ? 2 : (caps & 0x80) ? 1 : 0;
  }
}

// Walks the IE list: SSID, channel, security, IE fingerprint. Returns false if malformed.
bool parseIes(const uint8_t *p, size_t n, bool privacy, Mgmt &m) {
  bool rsn = false, wpa = false;
  uint32_t h = FNV_OFFSET;
  size_t i = 0;
  while (i + 2 <= n) {
    uint8_t id = p[i], len = p[i + 1];
    const uint8_t *d = p + i + 2;
    if (i + 2 + len > n) return false;
    if (id != 5) {  // TIM content and presence vary beacon to beacon
      h = fnv(h, id);
      if (id == 221 && len >= 4) for (int k = 0; k < 4; k++) h = fnv(h, d[k]);
    }
    if (id == 0 && len <= 32) {
      m.ssidLen = len;
      memcpy(m.ssid, d, len);
      m.ssid[len] = '\0';
    } else if (id == 3 && len >= 1) {
      m.channel = d[0];
    } else if (id == 48) {
      rsn = true;
      parseRsn(d, len, m);
    } else if (id == 221 && len >= 4 && d[0] == 0x00 && d[1] == 0x50 && d[2] == 0xF2 && d[3] == 1) {
      wpa = true;
    }
    i += 2 + len;
  }
  if (!rsn) m.sec = wpa ? SEC_WPA : privacy ? SEC_WEP : SEC_OPEN;
  m.ieHash = (uint16_t)(h ^ (h >> 16));
  m.hidden = true;
  for (uint8_t k = 0; k < m.ssidLen; k++) {
    if (m.ssid[k] != '\0') m.hidden = false;
  }
  return true;
}

}  // namespace

const char *secName(Sec s) {
  static const char *NAMES[] = {"OPN", "WEP", "W1", "W2", "W3", "W23", "ENT", "OWE"};
  return s <= SEC_OWE ? NAMES[s] : "?";
}

bool isBroadcast(const uint8_t mac[6]) {
  for (int i = 0; i < 6; i++) {
    if (mac[i] != 0xFF) return false;
  }
  return true;
}

uint32_t macHash(const uint8_t mac[6]) {
  uint32_t h = FNV_OFFSET;
  for (int i = 0; i < 6; i++) h = fnv(h, mac[i]);
  return h;
}

bool parseMgmt(const uint8_t *buf, size_t len, Mgmt &m) {
  memset(&m, 0, sizeof(m));
  if (len < 24) return false;
  uint8_t fc0 = buf[0], fc1 = buf[1];
  if (((fc0 >> 2) & 0x3) != 0) return false;  // type 0 = management
  switch (fc0 >> 4) {
    case 0: case 2: m.kind = ASSOC_REQ; break;
    case 1: case 3: m.kind = ASSOC_RESP; break;
    case 4: m.kind = PROBE_REQ; break;
    case 5: m.kind = PROBE_RESP; break;
    case 8: m.kind = BEACON; break;
    case 10: m.kind = DISASSOC; break;
    case 11: m.kind = AUTH; break;
    case 12: m.kind = DEAUTH; break;
    case 13: m.kind = ACTION; break;
    default: m.kind = OTHER;
  }
  m.retry = fc1 & 0x08;
  m.prot = fc1 & 0x40;
  memcpy(m.da, buf + 4, 6);
  memcpy(m.sa, buf + 10, 6);
  memcpy(m.bssid, buf + 16, 6);
  m.seq = le16(buf + 22) >> 4;
  const uint8_t *body = buf + 24;
  size_t n = len - 24;

  if (m.kind == BEACON || m.kind == PROBE_RESP) {
    if (n < 12) return false;
    m.tsf = le64(body);
    m.beaconInt = le16(body + 8);
    bool privacy = le16(body + 10) & 0x0010;
    return parseIes(body + 12, n - 12, privacy, m);
  }
  if (m.kind == PROBE_REQ) return parseIes(body, n, false, m);
  if ((m.kind == DEAUTH || m.kind == DISASSOC) && !m.prot && n >= 2) m.reason = le16(body);
  return true;
}

bool parseAdv(const uint8_t *d, size_t len, Adv &a) {
  memset(&a, 0, sizeof(a));
  size_t i = 0;
  while (i < len) {
    uint8_t l = d[i];
    if (l == 0) break;  // early terminator (zero padding)
    if (i + 1 + l > len) return false;
    uint8_t type = d[i + 1];
    const uint8_t *p = d + i + 2;
    size_t n = l - 1;
    if (type == 0xFF && n >= 2) {  // manufacturer specific data
      a.hasMfr = true;
      a.mfr = le16(p);
      // Apple Find My "offline finding" payload (AirTags and Find My accessories)
      if (a.mfr == 0x004C && n >= 3 && p[2] == 0x12) a.tracker = TRK_FINDMY;
    } else if ((type == 0x02 || type == 0x03) && n >= 2) {  // 16-bit service UUID lists
      for (size_t k = 0; k + 1 < n; k += 2) {
        uint16_t u = le16(p + k);
        if (u == 0xFEED || u == 0xFEEC) a.tracker = TRK_TILE;
        else if (u == 0xFD5A) a.tracker = TRK_SMARTTAG;
      }
    } else if (type == 0x16 && n >= 2) {  // 16-bit service data
      uint16_t u = le16(p);
      if (u == 0xFEED || u == 0xFEEC) a.tracker = TRK_TILE;
      else if (u == 0xFD5A) a.tracker = TRK_SMARTTAG;
      // Google Find My Device network: Eddystone service, frame type 0x40/0x41
      else if (u == 0xFEAA && n >= 3 && (p[2] == 0x40 || p[2] == 0x41)) a.tracker = TRK_GOOGLE;
    }
    i += 1 + l;
  }
  return true;
}

}  // namespace frames
