#pragma once

#include <math.h>

struct Stats {
  uint32_t sent[16];          // packets sent OK, by category
  uint32_t sendFail;          // UDP send failures
  uint32_t buildFail;         // packet could not be built
  uint32_t noTimeDrops;       // packets dropped because the clock is not synced
  uint32_t wifiConnects;
  uint32_t wifiDisconnects;
  uint32_t loopMaxUs;         // slowest loop() since the last report
  uint32_t lastTotalSent;
  uint32_t lastReportMs;
};