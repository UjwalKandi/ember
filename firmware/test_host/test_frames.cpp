// Host unit tests for the pure parsing / feature code (no ESP32 needed):
//   c++ -std=c++17 -Wall -I../src test_frames.cpp ../src/frames.cpp ../src/features.cpp -o /tmp/t && /tmp/t
// Also run by tests/test_rf.py when a C++ compiler is available.
#include <stdio.h>
#include <string.h>

#include <vector>

#include "features.h"
#include "frames.h"

static int fails = 0;
#define CHECK(c)                                              \
  do {                                                        \
    if (!(c)) {                                               \
      printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #c);     \
      fails++;                                                \
    }                                                         \
  } while (0)

using Bytes = std::vector<uint8_t>;

static const uint8_t AP[6] = {0xa1, 0xb2, 0xc3, 0xd4, 0xe5, 0xf6};
static const uint8_t BC[6] = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff};
static const uint8_t STA[6] = {0x02, 0x11, 0x22, 0x33, 0x44, 0x55};

static Bytes header(uint8_t subtype, const uint8_t *da, const uint8_t *sa, const uint8_t *bssid, uint16_t seq,
                    uint8_t flags = 0) {
  Bytes b = {(uint8_t)(subtype << 4), flags, 0, 0};
  b.insert(b.end(), da, da + 6);
  b.insert(b.end(), sa, sa + 6);
  b.insert(b.end(), bssid, bssid + 6);
  uint16_t sc = seq << 4;
  b.push_back(sc & 0xff);
  b.push_back(sc >> 8);
  return b;
}

static void ie(Bytes &b, uint8_t id, Bytes data) {
  b.push_back(id);
  b.push_back((uint8_t)data.size());
  b.insert(b.end(), data.begin(), data.end());
}

// RSN IE with one CCMP pairwise cipher and the given AKM suite types.
static Bytes rsn(std::vector<uint8_t> akms, uint16_t caps) {
  Bytes r = {1, 0, 0x00, 0x0f, 0xac, 4, 1, 0, 0x00, 0x0f, 0xac, 4, (uint8_t)akms.size(), 0};
  for (uint8_t a : akms) r.insert(r.end(), {0x00, 0x0f, 0xac, a});
  r.push_back(caps & 0xff);
  r.push_back(caps >> 8);
  return r;
}

static Bytes beacon(const char *ssid, uint8_t ch, uint16_t capInfo, Bytes sec, uint8_t secId, uint16_t seq = 1,
                    uint64_t tsf = 1000, bool extraIe = false, const uint8_t *bssid = AP) {
  Bytes b = header(8, BC, bssid, bssid, seq);
  for (int i = 0; i < 8; i++) b.push_back((tsf >> (8 * i)) & 0xff);
  b.insert(b.end(), {100, 0, (uint8_t)(capInfo & 0xff), (uint8_t)(capInfo >> 8)});
  ie(b, 0, Bytes(ssid, ssid + strlen(ssid)));
  ie(b, 1, {0x82, 0x84, 0x8b, 0x96});
  ie(b, 3, {ch});
  ie(b, 5, {0, 1, 0, 0});
  if (secId) ie(b, secId, sec);
  if (extraIe) ie(b, 221, {0x00, 0x10, 0x18, 2, 0});
  return b;
}

static frames::Mgmt parse(const Bytes &b) {
  frames::Mgmt m;
  CHECK(frames::parseMgmt(b.data(), b.size(), m));
  return m;
}

static void testBeacons() {
  frames::Mgmt m = parse(beacon("HomeNet", 6, 0x0011, rsn({2}, 0x0080), 48));
  CHECK(m.kind == frames::BEACON);
  CHECK(strcmp(m.ssid, "HomeNet") == 0 && !m.hidden);
  CHECK(m.channel == 6 && m.beaconInt == 100 && m.tsf == 1000);
  CHECK(m.sec == frames::SEC_WPA2 && m.pmf == 1);
  CHECK(strcmp(frames::secName(m.sec), "W2") == 0);

  CHECK(parse(beacon("x", 1, 0x0011, rsn({2, 8}, 0x00c0), 48)).sec == frames::SEC_WPA23);
  CHECK(parse(beacon("x", 1, 0x0011, rsn({2, 8}, 0x00c0), 48)).pmf == 2);
  CHECK(parse(beacon("x", 1, 0x0011, rsn({8}, 0x00c0), 48)).sec == frames::SEC_WPA3);
  CHECK(parse(beacon("x", 1, 0x0011, rsn({1}, 0), 48)).sec == frames::SEC_ENT);
  CHECK(parse(beacon("x", 1, 0x0011, rsn({18}, 0x00c0), 48)).sec == frames::SEC_OWE);
  CHECK(parse(beacon("x", 1, 0x0001, {}, 0)).sec == frames::SEC_OPEN);
  CHECK(parse(beacon("x", 1, 0x0011, {}, 0)).sec == frames::SEC_WEP);
  CHECK(parse(beacon("x", 1, 0x0011, {0x00, 0x50, 0xf2, 1, 1, 0}, 221)).sec == frames::SEC_WPA);

  // the TIM element changes every beacon and must not change the fingerprint
  Bytes a = beacon("x", 1, 0x0011, rsn({2}, 0), 48), b2 = a;
  b2[24 + 12 + 3 + 6 + 3 + 3] = 7;  // TIM DTIM count byte
  CHECK(parse(a).ieHash == parse(b2).ieHash);
  CHECK(parse(a).ieHash != parse(beacon("x", 1, 0x0011, rsn({2}, 0), 48, 1, 1000, true)).ieHash);

  frames::Mgmt h = parse(beacon("\0\0\0\0", 11, 0x0011, rsn({2}, 0), 48));
  CHECK(h.hidden && h.ssidLen == 0);  // strlen of "\0..." is 0 -> zero-length SSID
  Bytes hz = beacon("abcd", 11, 0x0011, rsn({2}, 0), 48);
  memset(&hz[24 + 12 + 2], 0, 4);     // same length, all NUL bytes
  CHECK(parse(hz).hidden && parse(hz).ssidLen == 4);
}

static void testOtherFrames() {
  Bytes d = header(12, BC, AP, AP, 9);
  d.insert(d.end(), {7, 0});
  frames::Mgmt m = parse(d);
  CHECK(m.kind == frames::DEAUTH && m.reason == 7 && !m.prot && frames::isBroadcast(m.da));

  Bytes p = header(12, STA, AP, AP, 9, 0x40);
  p.insert(p.end(), {0xde, 0xad, 0xbe, 0xef, 0, 0, 0, 0});
  m = parse(p);
  CHECK(m.kind == frames::DEAUTH && m.prot && m.reason == 0);

  Bytes pr = header(4, BC, STA, BC, 3);
  ie(pr, 0, {'C', 'a', 'f', 'e'});
  m = parse(pr);
  CHECK(m.kind == frames::PROBE_REQ && strcmp(m.ssid, "Cafe") == 0);

  CHECK(parse(header(10, STA, AP, AP, 1)).kind == frames::DISASSOC);
  CHECK(parse(header(11, AP, STA, AP, 1)).kind == frames::AUTH);
  CHECK(parse(header(2, AP, STA, AP, 1)).kind == frames::ASSOC_REQ);  // reassoc counts as assoc

  frames::Mgmt bad;
  Bytes data = header(0, BC, AP, AP, 1);
  data[0] = 0x08;  // type 2 = data
  CHECK(!frames::parseMgmt(data.data(), data.size(), bad));
  Bytes trunc = beacon("HomeNet", 6, 0x0011, rsn({2}, 0), 48);
  trunc.resize(trunc.size() - 3);  // last IE cut short
  CHECK(!frames::parseMgmt(trunc.data(), trunc.size(), bad));
  CHECK(!frames::parseMgmt(data.data(), 10, bad));
}

static void testExtractor() {
  features::Extractor x;
  x.begin(6, 0);
  Bytes b1 = beacon("HomeNet", 6, 0x0011, rsn({2, 8}, 0x00c0), 48, 100, 5000);
  x.add(parse(b1), -50, 10);
  x.add(parse(beacon("HomeNet", 6, 0x0011, rsn({2, 8}, 0x00c0), 48, 101, 107400)), -52, 112);
  features::Window w = x.finish(300);
  CHECK(w.ch == 6 && w.dtMs == 300);
  CHECK(w.cnt[frames::BEACON] == 2 && w.nb == 1 && w.nn == 1 && w.nss == 1);
  CHECK(w.sqa == 0 && w.tsj == 0 && w.iec == 0);
  CHECK(w.rmax == -50 && w.ravg == -51);
  CHECK(x.apCount() == 1);

  const features::Ap *ap = x.nextReport(300, 60000);
  CHECK(ap && strcmp(ap->ssid, "HomeNet") == 0 && ap->ch == 6 && ap->pmf == 2 && ap->sec == frames::SEC_WPA23);
  CHECK(x.nextReport(301, 60000) == nullptr);  // reported, unchanged

  // Attack window: spoofed broadcast deauths "from" the PMF-required AP with a foreign seq
  // counter, plus an impostor radio on the same BSSID (TSF restarts, different IE list).
  x.begin(6, 400);
  Bytes d = header(12, BC, AP, AP, 3000);
  d.insert(d.end(), {7, 0});
  for (int i = 0; i < 20; i++) x.add(parse(d), -40, 410 + i);
  x.add(parse(beacon("HomeNet", 6, 0x0011, rsn({2, 8}, 0x00c0), 48, 102, 60, true)), -45, 450);
  Bytes probe = header(4, BC, STA, BC, 3);
  ie(probe, 0, {});
  x.add(parse(probe), -70, 460);
  w = x.finish(700);
  CHECK(w.cnt[frames::DEAUTH] == 20 && w.bcast == 20 && w.spf == 20 && w.ds == 1);
  CHECK(w.sqa >= 2);   // jump to 3000, then 19 repeats (no retry bit), then back to 102
  CHECK(w.tsj == 1 && w.iec == 1 && w.nn == 0 && w.pd == 1);
  CHECK(memcmp(w.deaBssid, AP, 6) == 0);
  ap = x.nextReport(700, 60000);
  CHECK(ap != nullptr);  // IE fingerprint changed -> re-reported immediately

  // empty window
  x.begin(11, 800);
  w = x.finish(1100);
  CHECK(w.rmax == -127 && w.ravg == -127 && w.nb == 0);

  // stale refresh: seen again after its report, older than refresh -> due
  x.begin(6, 70000);
  x.add(parse(beacon("HomeNet", 6, 0x0011, rsn({2, 8}, 0x00c0), 48, 500, 99999, true)), -48, 70010);
  x.finish(70300);
  CHECK(x.nextReport(70300, 60000) != nullptr);

  // table eviction keeps working past MAX_APS distinct BSSIDs (beacon spam)
  x.begin(1, 80000);
  for (int i = 0; i < 100; i++) {
    uint8_t bs[6] = {0x02, 0, 0, 0, (uint8_t)(i >> 8), (uint8_t)i};
    x.add(parse(beacon("spam", 1, 0x0001, {}, 0, 1, 1000, false, bs)), -60, 80000 + i);
  }
  w = x.finish(80300);
  CHECK(w.nn == 100 && w.nb == features::MAX_DISTINCT && x.apCount() == features::MAX_APS);
}

static void testAdv() {
  frames::Adv a;
  uint8_t airtag[] = {0x02, 0x01, 0x06, 0x1e, 0xff, 0x4c, 0x00, 0x12, 0x19, 0x10};
  airtag[3] = 6;  // length covers type + 5 bytes present
  CHECK(frames::parseAdv(airtag, sizeof(airtag), a) && a.hasMfr && a.mfr == 0x004C && a.tracker == frames::TRK_FINDMY);
  uint8_t airpods[] = {0x07, 0xff, 0x4c, 0x00, 0x07, 0x19, 0x01, 0x02};
  CHECK(frames::parseAdv(airpods, sizeof(airpods), a) && a.tracker == frames::TRK_NONE);
  uint8_t tile[] = {0x02, 0x01, 0x06, 0x03, 0x03, 0xed, 0xfe};
  CHECK(frames::parseAdv(tile, sizeof(tile), a) && a.tracker == frames::TRK_TILE && !a.hasMfr);
  uint8_t tag[] = {0x05, 0x16, 0x5a, 0xfd, 0x10, 0x20};
  CHECK(frames::parseAdv(tag, sizeof(tag), a) && a.tracker == frames::TRK_SMARTTAG);
  uint8_t fmdn[] = {0x05, 0x16, 0xaa, 0xfe, 0x41, 0x00};
  CHECK(frames::parseAdv(fmdn, sizeof(fmdn), a) && a.tracker == frames::TRK_GOOGLE);
  uint8_t eddystoneUrl[] = {0x05, 0x16, 0xaa, 0xfe, 0x10, 0x00};
  CHECK(frames::parseAdv(eddystoneUrl, sizeof(eddystoneUrl), a) && a.tracker == frames::TRK_NONE);
  uint8_t bad[] = {0x09, 0xff, 0x4c};
  CHECK(!frames::parseAdv(bad, sizeof(bad), a));
  uint8_t padded[] = {0x02, 0x01, 0x06, 0x00, 0x00};
  CHECK(frames::parseAdv(padded, sizeof(padded), a));
}

int main() {
  testBeacons();
  testOtherFrames();
  testExtractor();
  testAdv();
  if (fails) {
    printf("%d check(s) failed\n", fails);
    return 1;
  }
  printf("all host tests passed\n");
  return 0;
}
