#include <Arduino.h>

#include "roles.h"

#if defined(EMBER_ROLE_GATEWAY)
void setup() { gateway::setup(); }
void loop() { gateway::loop(); }
#elif defined(EMBER_ROLE_NODE)
void setup() { node::setup(); }
void loop() { node::loop(); }
#else
#error "Build with -DEMBER_ROLE_NODE or -DEMBER_ROLE_GATEWAY (use the PlatformIO envs)"
#endif
