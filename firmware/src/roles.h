#pragma once

// Role entry points; main.cpp picks one at boot from the stored role.
namespace node {
void setup();
void loop();
}

namespace gateway {
void setup();
void loop();
}

namespace arm {  // v2 passive RF sensor arm
void setup();
void loop();
}
