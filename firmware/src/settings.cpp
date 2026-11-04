#include "settings.h"

#include <Preferences.h>

#include "config.h"
#include "sense_cam.h"

namespace settings {
namespace {

Preferences prefs;
Role curRole = NODE;
uint8_t curId = 0;
bool curSwap = false;
float curGain = 30.0f;

}  // namespace

void begin() {
  prefs.begin("ember", true);
  curRole = (Role)prefs.getUChar("role", NODE);
  curId = prefs.getUChar("id", NODE_ID);
  curSwap = prefs.getBool("cswap", false);
  curGain = prefs.getFloat("fgain", 30.0f);
  prefs.end();
}

Role role() { return curRole; }
uint8_t nodeId() { return curId; }
const char *roleName() { return curRole == GATEWAY ? "gateway" : "node"; }
bool camSwap() { return curSwap; }
float flameGain() { return curGain; }

void setCam(bool swap, float gain) {
  prefs.begin("ember", false);
  prefs.putBool("cswap", swap);
  prefs.putFloat("fgain", gain);
  prefs.end();
  curSwap = swap;
  curGain = gain;
  cam::configure(swap, gain);
}

void setRole(Role r) {
  prefs.begin("ember", false);
  prefs.putUChar("role", r);
  prefs.end();
  curRole = r;
}

void setId(uint8_t id) {
  prefs.begin("ember", false);
  prefs.putUChar("id", id);
  prefs.end();
  curId = id;
}

}  // namespace settings

namespace console {
namespace {

TestFn testFn = nullptr;
char line[256];
size_t lineLen = 0;

void printConfig() {
  Serial.printf("LOG|cfg role=%s id=%u fw=%s cam_swap=%d flame_gain=%.0f\n", settings::roleName(),
                settings::nodeId(), EMBER_FW, settings::camSwap(), settings::flameGain());
}

void reboot() {
  Serial.println("LOG|rebooting");
  Serial.flush();
  delay(200);
  ESP.restart();
}

// Returns true if the line was a common console command.
bool handleCommon(const char *l) {
  if (strcmp(l, "CFG?") == 0) {
    printConfig();
  } else if (strncmp(l, "CFG|id=", 7) == 0) {
    int id = atoi(l + 7);
    if (id < 1 || id > 254) {
      Serial.println("LOG|cfg error: id must be 1-254");
      return true;
    }
    settings::setId(id);
    printConfig();
    reboot();
  } else if (strcmp(l, "CFG|role=gateway") == 0 || strcmp(l, "CFG|role=node") == 0) {
    settings::setRole(strcmp(l + 9, "gateway") == 0 ? settings::GATEWAY : settings::NODE);
    printConfig();
    reboot();
  } else if (strncmp(l, "CFG", 3) == 0) {
    Serial.println("LOG|cfg error: use CFG?, CFG|id=<1-254>, CFG|role=node|gateway");
  } else if (strcmp(l, "CAM?") == 0) {
    cam::diag();
  } else if (strncmp(l, "CAM|swap=", 9) == 0) {
    settings::setCam(atoi(l + 9) == 1, settings::flameGain());
    printConfig();
  } else if (strncmp(l, "CAM|gain=", 9) == 0) {
    float g = atof(l + 9);
    if (g < 5 || g > 200) {
      Serial.println("LOG|cam error: gain must be 5-200");
      return true;
    }
    settings::setCam(settings::camSwap(), g);
    printConfig();
  } else if (strcmp(l, "TEST") == 0) {
    if (testFn) testFn();
  } else if (strcmp(l, "REBOOT") == 0) {
    reboot();
  } else {
    return false;
  }
  return true;
}

}  // namespace

void begin(TestFn selftest) { testFn = selftest; }

bool poll(char *out, size_t n) {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\r') continue;
    if (c != '\n') {
      if (lineLen < sizeof(line) - 1) line[lineLen++] = c;
      continue;
    }
    line[lineLen] = '\0';
    size_t len = lineLen;
    lineLen = 0;
    if (len == 0 || handleCommon(line)) continue;
    strncpy(out, line, n);
    out[n - 1] = '\0';
    return true;
  }
  return false;
}

}  // namespace console
