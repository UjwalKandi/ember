#pragma once
#include <Arduino.h>

// Hardware self-test, run with `TEST` on the USB console. Prints LOG|selftest lines and
// ends with `LOG|selftest PASS` or `LOG|selftest FAIL: <parts>`.
namespace selftest {

// Pass what the running role has already initialised so nothing is initialised twice;
// `needSensors` is false for the gateway (camera/mic failures don't fail the test).
void run(bool meshOk, bool &camOk, bool &micOk, bool needSensors);

}  // namespace selftest
