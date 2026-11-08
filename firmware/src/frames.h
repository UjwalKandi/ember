#pragma once
#include <stddef.h>
#include <stdint.h>

// Parsing of over-the-air bytes: 802.11 management frames and BLE advertisements.
// Pure C++ (no Arduino / ESP-IDF), so it is unit-tested on the host: firmware/test_host.
namespace frames {

enum Kind : uint8_t {
  OTHER = 0, BEACON, PROBE_REQ, PROBE_RESP, AUTH, ASSOC_REQ, ASSOC_RESP, DEAUTH, DISASSOC, ACTION,
  KIND_COUNT
};

enum Sec : uint8_t { SEC_OPEN = 0, SEC_WEP, SEC_WPA, SEC_WPA2, SEC_WPA3, SEC_WPA23, SEC_ENT, SEC_OWE };
const char *secName(Sec s);  // OPN WEP W1 W2 W3 W23 ENT OWE (the `sec:` token in A packets)

struct Mgmt {
  Kind kind;
  uint8_t da[6], sa[6], bssid[6];
  uint16_t seq;         // 12-bit sequence number
  bool retry;
  bool prot;            // Protected Frame bit (PMF-protected deauth/disassoc/action)
  uint16_t reason;      // deauth/disassoc reason code (0 if protected or absent)
  // beacon / probe response (probe request: ssid only)
  uint64_t tsf;
  uint16_t beaconInt;   // TU
  uint8_t ssidLen;
  char ssid[33];        // raw bytes, NUL-terminated; may contain any byte
  bool hidden;          // zero-length or all-NUL SSID
  uint8_t channel;      // DS Parameter Set, 0 if absent
  Sec sec;
  uint8_t pmf;          // 0 none, 1 capable, 2 required (RSN capabilities)
  uint16_t ieHash;      // fold of the ordered IE ids (+ vendor OUI/type); TIM excluded
};

// buf/len: the frame without FCS. Returns false for non-management or truncated frames.
bool parseMgmt(const uint8_t *buf, size_t len, Mgmt &out);

bool isBroadcast(const uint8_t mac[6]);
uint32_t macHash(const uint8_t mac[6]);  // FNV-1a, for distinct counting only (not privacy)

// BLE advertisements
enum Tracker : uint8_t { TRK_NONE = 0, TRK_FINDMY, TRK_TILE, TRK_SMARTTAG, TRK_GOOGLE };

struct Adv {
  bool hasMfr;
  uint16_t mfr;         // Bluetooth SIG company id (0x004C Apple, 0x0075 Samsung, ...)
  Tracker tracker;
};

// data/len: raw advertising data (AD structures). Returns false if malformed.
bool parseAdv(const uint8_t *data, size_t len, Adv &out);

}  // namespace frames
