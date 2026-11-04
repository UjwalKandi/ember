#include "settings.h"

#include <Preferences.h>

#include "config.h"

namespace settings {
namespace {

Preferences prefs;
Role curRole = NODE;
uint8_t curId = 0;

}  // namespace

void begin() {
  prefs.begin("ember", true);
  curRole = (Role)prefs.getUChar("role", NODE);
  curId = prefs.getUChar("id", NODE_ID);
  prefs.end();
}

Role role() { return curRole; }
uint8_t nodeId() { return curId; }
const char *roleName() { return curRole == GATEWAY ? "gateway" : "node"; }

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
  Serial.printf("LOG|cfg role=%s id=%u fw=%s\n", settings::roleName(), settings::nodeId(), EMBER_FW);
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
