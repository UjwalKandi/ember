// Sensor node: on-device detection, compact ELP events, command handling.
#ifdef EMBER_ROLE_NODE

#include <Arduino.h>

#include "config.h"
#include "mesh.h"
#include "roles.h"
#include "sense_cam.h"
#include "sense_mic.h"

namespace node {
namespace {

// Event codes in priority order with one threshold each (no hysteresis). These match
// gateway/sim.py SimNetwork.classify, the distribution Muse Glimmer was trained on.
enum Code { OK = 0, FLM, SMK, CRK, MOT };
const char *CODE_NAMES[] = {"OK", "FLM", "SMK", "CRK", "MOT"};
const uint8_t THRESHOLD[] = {0, 40, 25, 40, 40};
constexpr uint8_t REPORT_DELTA = 15;     // re-send same code if confidence moves this much
constexpr uint8_t GLARE_FLICKER = 8;     // flame burst spread below this = static glare
constexpr uint32_t SAMPLE_MS = 1000;
constexpr uint32_t MIC_WINDOW_MS = 200;

struct Scores {
  uint8_t s[5];  // indexed by Code; s[OK] unused
  uint8_t haze, snd;
};

struct PendingCmd {
  bool used;
  uint32_t dueMs;  // broadcast commands reply after a per-node delay
  int qid;
  char cmd[12];
  int rate;
  int lvl;
};

uint32_t seq = 0;
uint32_t heartbeatS = EMBER_HEARTBEAT_S;
uint32_t lastSample = 0, lastHeartbeat = 0;
uint8_t maxHaze = 0, maxSnd = 0;
bool camOk = false, micOk = false;

Code active = OK;
uint8_t lastSentConf = 0;
Code pending = OK;
uint8_t pendingCount = 0;

PendingCmd cmdQueue[4];
int alertLevel = 0;

// "|b3.9", or "" when no divider is fitted (a fake 0.0 V would read as a dying node).
void battToken(char *out, size_t n) {
#ifdef BATT_ADC_PIN
  snprintf(out, n, "|b%.1f", analogReadMilliVolts(BATT_ADC_PIN) * 2 / 1000.0f);
#else
  if (n) out[0] = '\0';
#endif
}

void send(const char *body) {
  mesh::send(body);
  Serial.printf("TX %s\n", body);  // local debug echo
}

Scores measure(uint32_t micWindowMs) {
  Scores sc = {};
  cam::Reading c = camOk ? cam::sample() : cam::Reading{false, 0, 0, 0};
  mesh::loop();
  mic::Reading m = micOk ? mic::sample(micWindowMs) : mic::Reading{false, 0, 0};
  mesh::loop();
  sc.s[FLM] = c.flame;
  sc.s[SMK] = c.haze;
  sc.s[CRK] = m.crackle;
  sc.s[MOT] = c.motion;
  sc.haze = c.haze;
  sc.snd = m.level;
  return sc;
}

Code classify(const Scores &sc) {
  for (int k = FLM; k <= MOT; k++) {
    if (sc.s[k] >= THRESHOLD[k]) return (Code)k;
  }
  return OK;
}

void sendEvent(Code code, const Scores &sc) {
  uint8_t conf = code == OK ? 0 : sc.s[code];
  char body[EMBER_MAX_BODY + 1];
  snprintf(body, sizeof(body), "E|n%d|s%lu|%s|c%d|hz%d|snd%d", NODE_ID, (unsigned long)++seq,
           CODE_NAMES[code], conf, sc.haze, sc.snd);
  send(body);
  lastSentConf = conf;
}

void sendHeartbeat() {
  char body[EMBER_MAX_BODY + 1], batt[12];
  battToken(batt, sizeof(batt));
  snprintf(body, sizeof(body), "H|n%d|s%lu%s|up%lu|hz%d|snd%d", NODE_ID,
           (unsigned long)++seq, batt, (unsigned long)(millis() / 1000), maxHaze, maxSnd);
  send(body);
  maxHaze = maxSnd = 0;
}

void tick() {
  Scores sc = measure(MIC_WINDOW_MS);
  maxHaze = max(maxHaze, sc.haze);
  maxSnd = max(maxSnd, sc.snd);

  Code cand = classify(sc);
  if (cand == active) {
    pendingCount = 0;
    if (active != OK && abs((int)sc.s[active] - (int)lastSentConf) > REPORT_DELTA) {
      sendEvent(active, sc);
    }
    return;
  }
  // Debounce: motion is momentary (1 tick), detections need 2, clearing needs 3.
  pendingCount = cand == pending ? pendingCount + 1 : 1;
  pending = cand;
  uint8_t needed = cand == MOT ? 1 : (cand == OK ? 3 : 2);
  if (pendingCount >= needed) {
    active = cand;
    pendingCount = 0;
    sendEvent(active, sc);
  }
}

// --- commands -----------------------------------------------------------

void onFrame(const mesh::Frame &f) {
  if (f.body[0] != 'C') return;
  char buf[EMBER_MAX_BODY + 1];
  strncpy(buf, f.body, sizeof(buf));
  buf[sizeof(buf) - 1] = '\0';

  PendingCmd cmd = {true, millis(), -1, "", -1, -1};
  char *save = nullptr;
  int idx = 0;
  for (char *tok = strtok_r(buf, "|", &save); tok; tok = strtok_r(nullptr, "|", &save), idx++) {
    if (idx == 0) continue;  // "C"
    if (idx == 1) {
      bool all = strcmp(tok, "*") == 0;
      if (!all && !(tok[0] == 'n' && atoi(tok + 1) == NODE_ID)) return;
      // Stagger replies to broadcasts so every node doesn't answer at once.
      if (all) cmd.dueMs += random(0, 50 * NODE_ID);
      continue;
    }
    if (tok[0] == 'q' && isdigit(tok[1])) cmd.qid = atoi(tok + 1);
    else if (strncmp(tok, "rate", 4) == 0) cmd.rate = atoi(tok + 4);
    else if (strncmp(tok, "lvl", 3) == 0) cmd.lvl = atoi(tok + 3);
    else if (isupper(tok[0]) && cmd.cmd[0] == '\0') strncpy(cmd.cmd, tok, sizeof(cmd.cmd) - 1);
  }
  if (cmd.cmd[0] == '\0') return;
  // Executed from loop(): CONFIRM blocks for seconds and must not run inside mesh::loop().
  for (auto &slot : cmdQueue) {
    if (!slot.used) {
      slot = cmd;
      return;
    }
  }
}

void confirm(int qid) {
  // ~3 s burst of 6 samples, averaged so one noisy frame can't decide the answer.
  constexpr int N = 6;
  uint32_t sum[5] = {}, hazeSum = 0, sndSum = 0;
  uint8_t flameMin = 255, flameMax = 0;
  for (int i = 0; i < N; i++) {
    Scores sc = measure(400);
    for (int k = FLM; k <= MOT; k++) sum[k] += sc.s[k];
    flameMin = min(flameMin, sc.s[FLM]);
    flameMax = max(flameMax, sc.s[FLM]);
    hazeSum += sc.haze;
    sndSum += sc.snd;
    delay(50);
  }
  Scores avg = {};
  for (int k = FLM; k <= MOT; k++) avg.s[k] = sum[k] / N;
  avg.haze = hazeSum / N;
  avg.snd = sndSum / N;
  // Real flames flicker; a static flame-coloured blob (sun glare, a lamp) does not.
  if (avg.s[FLM] >= THRESHOLD[FLM] && flameMax - flameMin < GLARE_FLICKER) avg.s[FLM] = 0;
  Code code = classify(avg);
  uint8_t conf = code == OK ? 0 : avg.s[code];
  char body[EMBER_MAX_BODY + 1];
  snprintf(body, sizeof(body), "R|n%d|q%d|CONF|%s|c%d|hz%d|snd%d", NODE_ID, qid,
           CODE_NAMES[code], conf, avg.haze, avg.snd);
  send(body);
}

void runCommand(const PendingCmd &cmd) {
  char body[EMBER_MAX_BODY + 1];
  if (strcmp(cmd.cmd, "PING") == 0) {
    char batt[12];
    battToken(batt, sizeof(batt));
    snprintf(body, sizeof(body), "R|n%d|q%d|PONG%s", NODE_ID, cmd.qid, batt);
    send(body);
  } else if (strcmp(cmd.cmd, "CONFIRM") == 0) {
    confirm(cmd.qid);
  } else if (strcmp(cmd.cmd, "SET") == 0) {
    // rate sets the heartbeat interval; detection sampling stays at 1 Hz for latency.
    if (cmd.rate > 0) heartbeatS = constrain(cmd.rate, 5, 600);
    snprintf(body, sizeof(body), "R|n%d|q%d|ACK", NODE_ID, cmd.qid);
    send(body);
  } else if (strcmp(cmd.cmd, "ALERT") == 0) {
    alertLevel = constrain(cmd.lvl, 0, 3);
    // Broadcast alerts are not acknowledged, to avoid a reply storm.
  }
}

void updateLed(uint32_t now) {
  // lvl N: N short blinks every 2 s. Active low.
  bool on = false;
  if (alertLevel > 0) {
    uint32_t phase = now % 2000;
    on = phase < (uint32_t)alertLevel * 300 && (phase % 300) < 150;
  }
  digitalWrite(LED_PIN, on ? LOW : HIGH);
}

}  // namespace

void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, HIGH);

  bool meshOk = mesh::begin(NODE_ID, onFrame);
  camOk = cam::begin();
  micOk = mic::begin();
  Serial.printf("EMBER node n%d mesh=%d cam=%d mic=%d\n", NODE_ID, meshOk, camOk, micOk);
  lastHeartbeat = millis() - heartbeatS * 1000;  // heartbeat right away
}

void loop() {
  uint32_t now = millis();
  mesh::loop();

  for (auto &slot : cmdQueue) {
    if (slot.used && (int32_t)(now - slot.dueMs) >= 0) {
      PendingCmd cmd = slot;
      slot.used = false;
      runCommand(cmd);
    }
  }

  if (now - lastSample >= SAMPLE_MS) {
    lastSample = now;
    tick();
  }
  if (now - lastHeartbeat >= heartbeatS * 1000) {
    lastHeartbeat = now;
    sendHeartbeat();
  }
  updateLed(now);
}

}  // namespace node

#endif  // EMBER_ROLE_NODE
