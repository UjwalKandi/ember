#include "selftest.h"

#include <WiFi.h>

#include "config.h"
#include "sense_cam.h"
#include "sense_mic.h"
#include "settings.h"

namespace selftest {

void run(bool meshOk, bool &camOk, bool &micOk, bool needSensors) {
  Serial.printf("LOG|selftest fw=%s role=%s id=%u chip=%s rev%d mac=%s\n", EMBER_FW,
                settings::roleName(), settings::nodeId(), ESP.getChipModel(), ESP.getChipRevision(),
                WiFi.macAddress().c_str());
  uint32_t psram = ESP.getPsramSize();
  Serial.printf("LOG|selftest heap=%u psram=%u (%s)\n", ESP.getFreeHeap(), psram,
                psram > 0 ? "ok" : "MISSING: camera needs PSRAM");
  Serial.printf("LOG|selftest mesh=%s\n", meshOk ? "ok" : "FAIL");

  if (!camOk) camOk = cam::begin();
  Serial.printf("LOG|selftest cam=%s\n", camOk ? "ok" : "FAIL (is the Sense board clipped on?)");
  if (camOk) {
    // the first frames after init only warm up the baseline, so report the last few
    for (int i = 0; i < 14; i++) {
      cam::Reading r = cam::sample();
      if (i >= 10) Serial.printf("LOG|selftest cam sample flame=%u haze=%u motion=%u\n", r.flame, r.haze, r.motion);
      delay(100);
    }
  }

  if (!micOk) micOk = mic::begin();
  Serial.printf("LOG|selftest mic=%s\n", micOk ? "ok" : "FAIL");
  if (micOk) {
    mic::Reading m = mic::sample(1000);
    Serial.printf("LOG|selftest mic sample level=%u crackle=%u (quiet room: level ~3-10)\n", m.level, m.crackle);
  }

  String fails;
  if (!meshOk) fails += "mesh ";
  if (needSensors) {
    if (psram == 0) fails += "psram ";
    if (!camOk) fails += "cam ";
    if (!micOk) fails += "mic ";
  }
  if (fails.length()) Serial.printf("LOG|selftest FAIL: %s\n", fails.c_str());
  else Serial.println("LOG|selftest PASS");
}

}  // namespace selftest
