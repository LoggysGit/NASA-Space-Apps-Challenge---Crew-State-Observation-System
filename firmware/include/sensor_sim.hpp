#pragma once

#include <Arduino.h>
#include <math.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>

#include "config.h"
#include "system.hpp"

// Sensor channel order is part of the telemetry protocol contract.
enum Channel : uint8_t {
  TEMP_T1, TEMP_T2, TEMP_T3, TEMP_T4,
  P1, P2, P3, P4,
  E1, E2, E3, E4, E5, E6,
  R1, IR1, R2, IR2,
  H1,
  VBAT,
  CHANNEL_COUNT
};

struct ChannelInfo {
  const char* name;
  const char* unit;
  float minValue;
  float maxValue;
  float step;
  uint8_t decimals;
};

static const ChannelInfo CHANNELS[CHANNEL_COUNT] = {
  {"t1", "C", 20.0f, 42.0f, 0.1f, 1},
  {"t2", "C", 20.0f, 42.0f, 0.1f, 1},
  {"t3", "C", 20.0f, 42.0f, 0.1f, 1},
  {"t4", "C", 20.0f, 42.0f, 0.1f, 1},
  {"p1", "Pa", 30000.0f, 125000.0f, 10.0f, 0},
  {"p2", "Pa", 30000.0f, 125000.0f, 10.0f, 0},
  {"p3", "Pa", 30000.0f, 125000.0f, 10.0f, 0},
  {"p4", "Pa", 30000.0f, 125000.0f, 10.0f, 0},
  {"e1", "uV", 0.0f, 5000.0f, 2.0f, 0},
  {"e2", "uV", 0.0f, 5000.0f, 2.0f, 0},
  {"e3", "uV", 0.0f, 5000.0f, 2.0f, 0},
  {"e4", "uV", 0.0f, 5000.0f, 2.0f, 0},
  {"e5", "uV", 0.0f, 5000.0f, 2.0f, 0},
  {"e6", "uV", 0.0f, 5000.0f, 2.0f, 0},
  {"r1", "cnt", 0.0f, 524287.0f, 100.0f, 0},
  {"ir1", "cnt", 0.0f, 524287.0f, 100.0f, 0},
  {"r2", "cnt", 0.0f, 524287.0f, 100.0f, 0},
  {"ir2", "cnt", 0.0f, 524287.0f, 100.0f, 0},
  {"h1", "%RH", 0.0f, 100.0f, 1.0f, 1},
  {"vBat", "mV", 3000.0f, 4400.0f, 10.0f, 0}
};

enum ScenarioId : uint8_t {
  RESTING = 0, HIGH_G, EXERTION, HYPOXIA, THERMAL, MUSCLE_FATIGUE,
  EMERGENCY, SCENARIO_COUNT
};

struct Scenario {
  const char* name;
  float heartRate;
  float spo2;
  float pressurePa;
  float emgScale;
  float ppgScale;
  float humidity;
  float skinTemp;
  float noiseScale;
  bool emergency;
};

static const Scenario SCENARIOS[SCENARIO_COUNT] = {
  {"Resting Normal", 68, 98, 101325, 1.0f, 1.0f, 45, 33.5f, 1.0f, false},
  {"High G-Force Load", 112, 95, 101500, 2.0f, 0.9f, 62, 34.5f, 1.8f, false},
  {"Extreme Exertion", 168, 95, 101200, 4.0f, 1.3f, 88, 36.0f, 2.2f, false},
  {"Hypoxia Simulation", 98, 82, 90000, 1.2f, 0.6f, 52, 32.0f, 1.4f, false},
  {"Thermal Stress", 118, 97, 101000, 1.5f, 1.4f, 96, 38.0f, 1.6f, false},
  {"Muscle Fatigue", 96, 97, 101300, 2.8f, 1.0f, 66, 34.2f, 1.3f, false},
  {"Emergency Malfunction", 148, 74, 85000, 4.5f, 0.4f, 97, 31.5f, 3.5f, true}
};

#define FLAG_SIMULATED  (1U << 0)
#define FLAG_MANUAL     (1U << 1)
#define FLAG_EMERGENCY  (1U << 2)
#define FLAG_RANDOMIZED (1U << 3)

// Stateful, instance-owned sensor model.
// Signals use bounded slow drift + low-amplitude measurement noise.
class SensorSimulator {
public:
  SensorSimulator()
    : random_(2463534242UL), scenario_(&SCENARIOS[RESTING]),
      heartRate_(68.0f), spo2_(98.0f), pulsePhase_(0.0f), tremorPhase_(0.0f),
      lastSampleMs_(0), lastBatteryMs_(0), scenarioId_(RESTING),
      manualMode_(false), randomizedMode_(false) {
    memset(base_, 0, sizeof(base_));
    memset(live_, 0, sizeof(live_));
    memset(wander_, 0, sizeof(wander_));
    memset(noise_, 0, sizeof(noise_));
  }

  void seedRandom(uint32_t seed) { random_ = Util(seed ? seed : 1U); }

  void begin() {
    base_[VBAT] = 4100.0f;
    applyScenario(RESTING);
    lastSampleMs_ = millis();
    lastBatteryMs_ = lastSampleMs_;
  }

  bool sampleDue(uint32_t now, uint32_t periodMs) const {
    return (uint32_t)(now - lastSampleMs_) >= periodMs;
  }

  void setLastSampleTime(uint32_t value) { lastSampleMs_ = value; }

  void sample(uint32_t now) {
    uint32_t elapsedMs = (uint32_t)(now - lastSampleMs_);
    float dt = (float)elapsedMs * 0.001f;
    lastSampleMs_ = now;
    if (dt <= 0.0f || dt > 1.0f) dt = SAMPLE_PERIOD_MS * 0.001f;
    // Avoid a huge simulated jump after a paused/debugged loop.
    if (dt > 0.1f) dt = 0.1f;

    pulsePhase_ += dt * (heartRate_ / 60.0f);
    pulsePhase_ -= floorf(pulsePhase_);
    tremorPhase_ += dt * TWO_PI_F * 8.0f;
    while (tremorPhase_ >= TWO_PI_F) tremorPhase_ -= TWO_PI_F;

    // Slow bounded random walk. Rates are expressed per second, so behaviour
    // does not depend strongly on the configured sample frequency.
    for (uint8_t i = 0; i < CHANNEL_COUNT; ++i) {
      float maxRate = 0.0f;
      float wanderLimit = 0.0f;
      float measurementSigma = 0.0f;

      if (i <= TEMP_T4) {                 // skin temperature
        maxRate = 0.025f;                 // deg C / second
        wanderLimit = 0.45f;
        measurementSigma = 0.012f;
      } else if (i <= P4) {               // pressure
        maxRate = 8.0f;                    // Pa / second
        wanderLimit = 180.0f;
        measurementSigma = 1.5f;
      } else if (i <= E6) {               // EMG envelope
        maxRate = fmaxf(1.0f, base_[i] * 0.025f);
        wanderLimit = fmaxf(4.0f, base_[i] * 0.18f);
        measurementSigma = fmaxf(0.35f, base_[i] * 0.006f);
      } else if (i <= IR2) {              // optical DC level
        maxRate = base_[i] * 0.0015f;
        wanderLimit = base_[i] * 0.025f;
        measurementSigma = base_[i] * 0.00035f;
      } else if (i == H1) {                // relative humidity
        maxRate = 0.08f;
        wanderLimit = 2.0f;
        measurementSigma = 0.05f;
      }

      if (i != VBAT) {
        wander_[i] += random_.randomFloat(-maxRate, maxRate) * dt;
        wander_[i] = clamp(wander_[i], -wanderLimit, wanderLimit);
        // Small correlated sensor noise; gaussianNoise() is bounded to +/-3.
        noise_[i] = 0.72f * noise_[i] +
                    0.28f * random_.gaussianNoise() * measurementSigma * scenario_->noiseScale;
      } else {
        noise_[i] = 0.0f;
      }
    }

    const float pulse = sinf(pulsePhase_ * TWO_PI_F);
    for (uint8_t i = TEMP_T1; i <= TEMP_T4; ++i) {
      live_[i] = base_[i] + wander_[i] + noise_[i];
    }
    for (uint8_t i = P1; i <= P4; ++i) {
      // These are pressure transducers: only a subtle pulse component.
      live_[i] = base_[i] + wander_[i] + noise_[i] + 3.0f * pulse;
    }
    for (uint8_t i = E1; i <= E6; ++i) {
      const float activity = 1.0f + 0.035f * sinf(tremorPhase_ + (float)i * 0.73f);
      live_[i] = (base_[i] + wander_[i]) * activity + noise_[i];
    }

    const float spo2Ratio = clamp((110.0f - spo2_) / 25.0f, 0.25f, 1.6f);
    const float ac = 0.010f * scenario_->ppgScale;
    live_[R1]  = (base_[R1]  + wander_[R1])  * (1.0f + ac * spo2Ratio * pulse) + noise_[R1];
    live_[IR1] = (base_[IR1] + wander_[IR1]) * (1.0f + ac * pulse) + noise_[IR1];
    live_[R2]  = (base_[R2]  + wander_[R2])  * (1.0f + ac * spo2Ratio * pulse) + noise_[R2];
    live_[IR2] = (base_[IR2] + wander_[IR2]) * (1.0f + ac * pulse) + noise_[IR2];
    live_[H1] = base_[H1] + wander_[H1] + noise_[H1];

    if ((uint32_t)(now - lastBatteryMs_) >= BATTERY_PERIOD_MS) {
      lastBatteryMs_ = now;
      base_[VBAT] = clamp(base_[VBAT] - random_.randomFloat(0.0f, 0.25f), 3300.0f, 4200.0f);
    }
    live_[VBAT] = base_[VBAT];

    for (uint8_t i = 0; i < CHANNEL_COUNT; ++i) {
      live_[i] = clamp(live_[i], CHANNELS[i].minValue, CHANNELS[i].maxValue);
    }
  }

  void applyScenario(uint8_t id) {
    if (id >= SCENARIO_COUNT) id = RESTING;
    scenarioId_ = id;
    scenario_ = &SCENARIOS[id];
    manualMode_ = false;
    randomizedMode_ = false;
    generateBaselines();
    LOGI("SENS", "scenario applied: %s (HR %.0f, SpO2 %.0f)", scenario_->name, scenario_->heartRate, scenario_->spo2);
  }

  void randomize() {
    // Randomize around the selected scenario, not across the entire ADC range.
    for (uint8_t i = 0; i < 4; ++i) {
      base_[TEMP_T1 + i] = clamp(scenario_->skinTemp + ((float)i - 1.5f) * 0.18f + random_.randomFloat(-0.35f, 0.35f), 28.0f, 40.0f);
      base_[P1 + i] = clamp(scenario_->pressurePa + random_.randomFloat(-120.0f, 120.0f), 30000.0f, 125000.0f);
    }
    const float emgCenter = (scenarioId_ == RESTING) ? 18.0f : 90.0f * scenario_->emgScale;
    for (uint8_t i = 0; i < 6; ++i) {
      base_[E1 + i] = clamp(emgCenter * random_.randomFloat(0.75f, 1.25f), 2.0f, 1800.0f);
    }
    const float redDc = random_.randomFloat(52000.0f, 76000.0f);
    const float irDc = random_.randomFloat(85000.0f, 125000.0f);
    base_[R1] = redDc;
    base_[IR1] = irDc;
    base_[R2] = redDc * random_.randomFloat(0.97f, 1.03f);
    base_[IR2] = irDc * random_.randomFloat(0.97f, 1.03f);
    base_[H1] = clamp(scenario_->humidity + random_.randomFloat(-3.0f, 3.0f), 15.0f, 100.0f);
    heartRate_ = clamp(scenario_->heartRate + random_.randomFloat(-4.0f, 4.0f), 45.0f, 190.0f);
    spo2_ = clamp(scenario_->spo2 + random_.randomFloat(-0.8f, 0.8f), 70.0f, 100.0f);
    memset(wander_, 0, sizeof(wander_));
    memset(noise_, 0, sizeof(noise_));
    memcpy(live_, base_, sizeof(live_));
    randomizedMode_ = true;
    manualMode_ = false;
    LOGI("SENS", "values randomized: HR %.0f, SpO2 %.1f", heartRate_, spo2_);
  }

  void setBase(uint8_t channel, float value) {
    if (channel >= CHANNEL_COUNT) return;
    base_[channel] = clamp(value, CHANNELS[channel].minValue, CHANNELS[channel].maxValue);
    wander_[channel] = 0.0f;
    noise_[channel] = 0.0f;
    live_[channel] = base_[channel];
    manualMode_ = true;
    randomizedMode_ = false;
  }

  float channelValue(uint8_t channel) const { return channel < CHANNEL_COUNT ? live_[channel] : 0.0f; }
  float baseValue(uint8_t channel) const { return channel < CHANNEL_COUNT ? base_[channel] : 0.0f; }
  uint8_t scenarioId() const { return scenarioId_; }
  uint8_t flags() const {
    return (uint8_t)(FLAG_SIMULATED | (manualMode_ ? FLAG_MANUAL : 0) |
                     (scenario_->emergency ? FLAG_EMERGENCY : 0) |
                     (randomizedMode_ ? FLAG_RANDOMIZED : 0));
  }
  const char* scenarioName() const {
    if (randomizedMode_) return "Randomized";
    if (manualMode_) return "Custom";
    return scenario_->name;
  }
  float heartRate() const { return heartRate_; }
  float spo2() const { return spo2_; }
  static uint8_t channelCount() { return CHANNEL_COUNT; }
  static uint8_t scenarioCount() { return SCENARIO_COUNT; }
  static const ChannelInfo& channelInfo(uint8_t i) { return CHANNELS[i < CHANNEL_COUNT ? i : 0]; }
  static const Scenario& scenarioInfo(uint8_t i) { return SCENARIOS[i < SCENARIO_COUNT ? i : RESTING]; }

private:
  Util random_;
  const Scenario* scenario_;
  float base_[CHANNEL_COUNT];
  float live_[CHANNEL_COUNT];
  float wander_[CHANNEL_COUNT];
  float noise_[CHANNEL_COUNT];
  float heartRate_;
  float spo2_;
  float pulsePhase_;
  float tremorPhase_;
  uint32_t lastSampleMs_;
  uint32_t lastBatteryMs_;
  uint8_t scenarioId_;
  bool manualMode_;
  bool randomizedMode_;

  float clamp(float value, float lo, float hi) const { return random_.clampFloat(value, lo, hi); }

  void generateBaselines() {
    heartRate_ = scenario_->heartRate;
    spo2_ = scenario_->spo2;
    for (uint8_t i = 0; i < 4; ++i) {
      base_[TEMP_T1 + i] = scenario_->skinTemp + ((float)i - 1.5f) * 0.18f;
      base_[P1 + i] = scenario_->pressurePa - (float)i * 8.0f;
    }
    for (uint8_t i = 0; i < 6; ++i) {
      const float center = (scenarioId_ == RESTING) ? 18.0f : 90.0f * scenario_->emgScale;
      base_[E1 + i] = center * random_.randomFloat(0.90f, 1.10f);
    }
    base_[R1] = 65000.0f;
    base_[IR1] = 105000.0f;
    base_[R2] = 63500.0f;
    base_[IR2] = 102000.0f;
    base_[H1] = scenario_->humidity;
    if (base_[VBAT] < 3300.0f) base_[VBAT] = 4100.0f;
    memset(wander_, 0, sizeof(wander_));
    memset(noise_, 0, sizeof(noise_));
    memcpy(live_, base_, sizeof(live_));
  }
};