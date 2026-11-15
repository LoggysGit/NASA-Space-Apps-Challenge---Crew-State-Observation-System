#pragma once

#include <Arduino.h>
#include <math.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <time.h>
#include <sys/time.h>

#include "config.h"

// =============================================================================
// Stats
// Runtime counters shared by firmware subsystems.
// =============================================================================

class Stats {
public:
  uint32_t sent[16];
  uint32_t sendFail;
  uint32_t buildFail;
  uint32_t noTimeDrops;
  uint32_t wifiConnects;
  uint32_t wifiDisconnects;
  uint32_t loopMaxUs;
  uint32_t lastTotalSent;
  uint32_t lastReportMs;

  Stats() {
    reset();
  }

  void reset() {
    memset(sent, 0, sizeof(sent));
    sendFail = 0;
    buildFail = 0;
    noTimeDrops = 0;
    wifiConnects = 0;
    wifiDisconnects = 0;
    loopMaxUs = 0;
    lastTotalSent = 0;
    lastReportMs = 0;
  }

  uint32_t totalSent() const {
    uint32_t total = 0;
    for (uint8_t i = 0; i < 16; ++i) {
      total += sent[i];
    }
    return total;
  }
};

// =============================================================================
// Util
// Small math, random, serialization and checksum utilities.
//
// RNG state is an instance field: each Util object owns its sequence.
// The default seed matches the original firmware.
// =============================================================================

class Util {
public:
  explicit Util(uint32_t seed = 2463534242UL)
    : rngState(seed) {
    if (rngState == 0) {
      rngState = 2463534242UL;
    }
  }

  bool rateLimit(uint32_t& last, uint32_t now, uint32_t periodMs) const {
    if (last != 0 && now - last < periodMs) {
      return false;
    }

    last = now;
    return true;
  }

  uint32_t rngNext() {
    // xorshift32: same operations and default seed as the original sketch.
    rngState ^= rngState << 13;
    rngState ^= rngState >> 17;
    rngState ^= rngState << 5;
    return rngState;
  }

  float randomFloat(float lo, float hi) {
    return lo + (hi - lo) * ((float)rngNext() * 2.3283064e-10f);
  }

  // Sum of six uniform values -> approximately normal, range -3..+3.
  float gaussianNoise() {
    float sum = 0.0f;

    for (uint8_t i = 0; i < 6; ++i) {
      sum += randomFloat(0.0f, 1.0f);
    }

    return sum - 3.0f;
  }

  float clampFloat(float value, float lo, float hi) const {
    return value < lo ? lo : (value > hi ? hi : value);
  }

  uint16_t toU16(float value, float scale) const {
    long q = lroundf(value * scale);

    if (q < 0) {
      q = 0;
    }

    if (q > 65535) {
      q = 65535;
    }

    return (uint16_t)q;
  }

  uint32_t toU32(float value, float scale) const {
    double q = round((double)value * scale);

    if (q < 0.0) {
      q = 0.0;
    }

    if (q > 4294967295.0) {
      q = 4294967295.0;
    }

    return (uint32_t)q;
  }

  uint8_t* putU8(uint8_t* p, uint8_t value) const {
    *p++ = value;
    return p;
  }

  uint8_t* putU16(uint8_t* p, uint16_t value) const {
    *p++ = (uint8_t)(value >> 8);
    *p++ = (uint8_t)value;
    return p;
  }

  uint8_t* putU32(uint8_t* p, uint32_t value) const {
    p = putU16(p, (uint16_t)(value >> 16));
    return putU16(p, (uint16_t)value);
  }

  // CRC-16/CCITT-FALSE: init 0xFFFF, poly 0x1021, no reflection, xorout 0.
  uint16_t crc16(const uint8_t* data, size_t len) const {
    uint16_t crc = 0xFFFF;

    while (len--) {
      crc ^= (uint16_t)(*data++) << 8;

      for (uint8_t bit = 0; bit < 8; ++bit) {
        crc = (crc & 0x8000)
          ? (uint16_t)((crc << 1) ^ 0x1021)
          : (uint16_t)(crc << 1);
      }
    }

    return crc;
  }

  uint32_t seed() const {
    return rngState;
  }

private:
  uint32_t rngState;
};

// =============================================================================
// TimeCode
// CCSDS CUC Level 1 timestamp and clock synchronization service.
// =============================================================================

class TimeCode {
public:
  TimeCode()
    : wasSynced(false),
      lastWarnMs(0) {
  }

  bool isSynced() const {
    timeval tv;

    if (gettimeofday(&tv, nullptr) != 0) {
      return false;
    }

    return tv.tv_sec >= MIN_VALID_UNIX_TIME;
  }

  // Writes 7 bytes: P-field + 4 coarse bytes + 2 fine bytes.
  bool write(uint8_t* out) const {
    timeval tv;

    if (gettimeofday(&tv, nullptr) != 0 ||
        tv.tv_sec < MIN_VALID_UNIX_TIME) {
      return false;
    }

    const uint64_t coarse =
      (uint64_t)tv.tv_sec +
      UNIX_TO_CCSDS_EPOCH_S +
      TAI_MINUS_UTC_S;

    const uint32_t fine =
      (uint32_t)(((uint64_t)tv.tv_usec * 65536ULL) / 1000000ULL);

    out[0] = 0x1E; // CUC, 1958 epoch, 4 coarse + 2 fine octets

    uint8_t* p = out + 1;
    p = putU32(p, (uint32_t)coarse);
    putU16(p, (uint16_t)fine);

    return true;
  }

  // Call periodically from the main loop. Wi-Fi state is passed in explicitly
  // so this class does not depend on a global WiFi object.
  void service(uint32_t now, bool wifiConnected) {
    const bool synced = isSynced();

    if (synced != wasSynced) {
      wasSynced = synced;

      if (synced) {
        LOGI("TIME", "clock synchronized (unix=%ld)", (long)time(nullptr));
      } else {
        LOGW("TIME", "clock lost");
      }
    }

    if (!synced && wifiConnected &&
        rateLimitWarning(now, 5000)) {
      LOGW("TIME", "waiting for NTP - telemetry packets are NOT sent until the clock is set");
    }
  }

private:
  bool wasSynced;
  uint32_t lastWarnMs;

  bool rateLimitWarning(uint32_t now, uint32_t periodMs) {
    if (lastWarnMs != 0 && now - lastWarnMs < periodMs) {
      return false;
    }

    lastWarnMs = now;
    return true;
  }

  static uint8_t* putU16(uint8_t* p, uint16_t value) {
    *p++ = (uint8_t)(value >> 8);
    *p++ = (uint8_t)value;
    return p;
  }

  static uint8_t* putU32(uint8_t* p, uint32_t value) {
    p = putU16(p, (uint16_t)(value >> 16));
    return putU16(p, (uint16_t)value);
  }
};

// =============================================================================
// System facade
// Keeps shared runtime services and statistics together for Firmware.
// =============================================================================

class System {
public:
  System() : util_(2463534242UL) {}

  void seedRandom(uint32_t seed) {
    util_ = Util(seed ? seed : 1U);
  }

  Stats& stats() { return stats_; }
  const Stats& stats() const { return stats_; }

  bool timeSynced() const { return timeCode_.isSynced(); }
  bool writeTimeCode(uint8_t* output) const { return timeCode_.write(output); }
  void serviceTime(uint32_t now, bool wifiConnected) {
    timeCode_.service(now, wifiConnected);
  }

private:
  Util util_;
  Stats stats_;
  TimeCode timeCode_;
};
