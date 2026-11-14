#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <TFT_eSPI.h>
#include <EncButton.h>
#include <esp_system.h>
#include <math.h>
#include <string.h>
#include <time.h>
#include <sys/time.h>

// =============================================================================
// Configuration
// =============================================================================

#ifndef DEVICE_ID
#define DEVICE_ID 1
#endif

#ifndef DEVICE_NAME
#define DEVICE_NAME "-"
#endif

static_assert(DEVICE_ID >= 0 && DEVICE_ID <= 126,
              "DEVICE_ID must be 0..126; APID 0x7FF is reserved");

constexpr uint8_t DEVICE_ID_U8 = static_cast<uint8_t>(DEVICE_ID);
constexpr size_t DEVICE_NAME_LEN = 16;

constexpr char WIFI_SSID[] = "CHANGE_ME";
constexpr char WIFI_PASS[] = "CHANGE_ME";
IPAddress DEST_IP(192, 168, 0, 161);

constexpr uint16_t UDP_DEST_PORT = 5005;
constexpr uint16_t UDP_SOURCE_PORT_BASE = 40000;

constexpr uint32_t SAMPLE_PERIOD_MS = 40;       // 25 Hz
constexpr uint32_t BATTERY_PERIOD_MS = 1000;    // 1 Hz
constexpr uint32_t INFO_PERIOD_MS = 2000;
constexpr uint32_t WIFI_RETRY_MS = 8000;

constexpr uint8_t BTN_A_PIN = 32;
constexpr uint8_t BTN_B_PIN = 33;

constexpr bool SIMULATION_MODE = true;

// CCSDS CUC Level 1 configuration.
// TAI-UTC must be updated if the official leap-second offset changes.
constexpr uint8_t TAI_MINUS_UTC_SECONDS = 37;

// =============================================================================
// Helpers
// =============================================================================

static float randomFloat(float lo, float hi) {
  const float unit =
    static_cast<float>(esp_random()) / 4294967295.0f;
  return lo + (hi - lo) * unit;
}

static float gaussianNoise() {
  float sum = 0.0f;
  for (uint8_t i = 0; i < 6; ++i) {
    sum += randomFloat(0.0f, 1.0f);
  }
  return (sum - 3.0f);
}

static float clampFloat(float value, float lo, float hi) {
  return value < lo ? lo : (value > hi ? hi : value);
}

static uint16_t toU16(float value, float scale) {
  long q = lroundf(value * scale);
  if (q < 0) q = 0;
  if (q > 65535) q = 65535;
  return static_cast<uint16_t>(q);
}

static uint32_t toU32(float value, float scale) {
  double q = round(static_cast<double>(value) * scale);
  if (q < 0.0) q = 0.0;
  if (q > 4294967295.0) q = 4294967295.0;
  return static_cast<uint32_t>(q);
}

// Big-endian serialization.
static uint8_t* putU8(uint8_t* p, uint8_t value) {
  *p++ = value;
  return p;
}

static uint8_t* putU16(uint8_t* p, uint16_t value) {
  *p++ = static_cast<uint8_t>(value >> 8);
  *p++ = static_cast<uint8_t>(value);
  return p;
}

static uint8_t* putU32(uint8_t* p, uint32_t value) {
  p = putU16(p, static_cast<uint16_t>(value >> 16));
  return putU16(p, static_cast<uint16_t>(value));
}

static uint16_t crc16CcittFalse(const uint8_t* data, size_t length) {
  uint16_t crc = 0xFFFF;

  while (length--) {
    crc ^= static_cast<uint16_t>(*data++) << 8;

    for (uint8_t bit = 0; bit < 8; ++bit) {
      crc = (crc & 0x8000)
        ? static_cast<uint16_t>((crc << 1) ^ 0x1021)
        : static_cast<uint16_t>(crc << 1);
    }
  }

  return crc;
}

// =============================================================================
// CCSDS CUC Level 1 Time Code
// =============================================================================

class CcsdsTimeCode {
public:
  static constexpr size_t SIZE = 7;
  static constexpr uint64_t UNIX_TO_CCSDS_EPOCH_SECONDS = 378691200ULL;

  static bool synchronized() {
    timeval tv{};
    if (gettimeofday(&tv, nullptr) != 0) return false;

    // Reject default/uninitialized clocks.
    return tv.tv_sec >= 1704067200;
  }

  // Writes: P-field (1 byte) + coarse time (4 bytes) + fine time (2 bytes).
  // Coarse time is seconds since 1958-01-01 00:00:00 TAI.
  static bool write(uint8_t* output, size_t capacity) {
    if (!output || capacity < SIZE || !synchronized()) {
      return false;
    }

    timeval tv{};
    if (gettimeofday(&tv, nullptr) != 0) return false;

    const uint64_t coarse =
      static_cast<uint64_t>(tv.tv_sec) +
      UNIX_TO_CCSDS_EPOCH_SECONDS +
      TAI_MINUS_UTC_SECONDS;

    const uint32_t fine = static_cast<uint32_t>(
      (static_cast<uint64_t>(tv.tv_usec) * 65536ULL) / 1000000ULL
    );

    // CUC P-field: 4-byte coarse time, 2-byte fine time.
    output[0] = 0x1E;

    uint8_t* p = output + 1;
    p = putU32(p, static_cast<uint32_t>(coarse));
    p = putU16(p, static_cast<uint16_t>(fine));

    return true;
  }
};

// =============================================================================
// Channel and scenario model
// =============================================================================

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
  {"t1", "C",     20.0f, 42.0f, 0.1f, 1},
  {"t2", "C",     20.0f, 42.0f, 0.1f, 1},
  {"t3", "C",     20.0f, 42.0f, 0.1f, 1},
  {"t4", "C",     20.0f, 42.0f, 0.1f, 1},
  {"p1", "Pa", 30000.0f, 125000.0f, 10.0f, 0},
  {"p2", "Pa", 30000.0f, 125000.0f, 10.0f, 0},
  {"p3", "Pa", 30000.0f, 125000.0f, 10.0f, 0},
  {"p4", "Pa", 30000.0f, 125000.0f, 10.0f, 0},
  {"e1", "uV",     0.0f, 5000.0f, 2.0f, 0},
  {"e2", "uV",     0.0f, 5000.0f, 2.0f, 0},
  {"e3", "uV",     0.0f, 5000.0f, 2.0f, 0},
  {"e4", "uV",     0.0f, 5000.0f, 2.0f, 0},
  {"e5", "uV",     0.0f, 5000.0f, 2.0f, 0},
  {"e6", "uV",     0.0f, 5000.0f, 2.0f, 0},
  {"r1", "cnt",    0.0f, 524287.0f, 100.0f, 0},
  {"ir1","cnt",    0.0f, 524287.0f, 100.0f, 0},
  {"r2", "cnt",    0.0f, 524287.0f, 100.0f, 0},
  {"ir2","cnt",    0.0f, 524287.0f, 100.0f, 0},
  {"h1", "%RH",    0.0f, 100.0f, 1.0f, 1},
  {"vBat","mV",  3000.0f, 4400.0f, 10.0f, 0}
};

enum ScenarioId : uint8_t {
  RESTING = 0,
  HIGH_G,
  EXERTION,
  HYPOXIA,
  THERMAL,
  MUSCLE_FATIGUE,
  EMERGENCY,
  SCENARIO_COUNT
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
  {"Resting Normal",       68, 98, 101325, 1.0f, 1.0f, 45, 33.5f, 1.0f, false},
  {"High G-Force Load",   112, 95, 101500, 2.0f, 0.9f, 62, 34.5f, 1.8f, false},
  {"Extreme Exertion",    168, 95, 101200, 4.0f, 1.3f, 88, 36.0f, 2.2f, false},
  {"Hypoxia Simulation",   98, 82,  90000, 1.2f, 0.6f, 52, 32.0f, 1.4f, false},
  {"Thermal Stress",      118, 97, 101000, 1.5f, 1.4f, 96, 38.0f, 1.6f, false},
  {"Muscle Fatigue",       96, 97, 101300, 2.8f, 1.0f, 66, 34.2f, 1.3f, false},
  {"Emergency Malfunction",148,74, 85000, 4.5f, 0.4f, 97, 31.5f, 3.5f, true}
};

enum DataFlags : uint8_t {
  FLAG_SIMULATED = 1 << 0,
  FLAG_MANUAL = 1 << 1,
  FLAG_EMERGENCY = 1 << 2,
  FLAG_RANDOMIZED = 1 << 3
};

// =============================================================================
// SensorManager
// =============================================================================

class SensorManager {
public:
  void begin() {
    applyScenario(RESTING);
    lastSampleMs_ = millis();
    lastBatteryMs_ = lastSampleMs_;
  }

  void applyScenario(uint8_t id) {
    if (id >= SCENARIO_COUNT) id = RESTING;

    scenarioId_ = id;
    scenario_ = SCENARIOS[id];
    manual_ = false;
    randomized_ = false;

    generateBaselines();
  }

  void randomize() {
    const Scenario& s = scenario_;
    const float tempSpread =
      (scenarioId_ == RESTING) ? 0.45f : 0.8f;
    const float pressureSpread =
      (scenarioId_ == RESTING) ? 180.0f : 450.0f;

    for (uint8_t i = 0; i < 4; ++i) {
      base_[TEMP_T1 + i] = clampFloat(
        s.skinTemp + (static_cast<float>(i) - 1.5f) * 0.25f +
        randomFloat(-tempSpread, tempSpread),
        28.0f, 40.0f
      );

      base_[P1 + i] = clampFloat(
        s.pressurePa + randomFloat(-pressureSpread, pressureSpread),
        30000.0f, 125000.0f
      );
    }

    const float emgLo = scenarioId_ == RESTING ? 5.0f : 10.0f;
    const float emgHi = scenarioId_ == RESTING ? 35.0f : 1200.0f;

    for (uint8_t i = 0; i < 6; ++i) {
      base_[E1 + i] = randomFloat(emgLo, emgHi) * s.emgScale;
      base_[E1 + i] = clampFloat(base_[E1 + i], 0.0f, 5000.0f);
    }

    const float redDc = randomFloat(45000.0f, 95000.0f);
    const float irDc = randomFloat(70000.0f, 145000.0f);

    base_[R1] = redDc;
    base_[IR1] = irDc;
    base_[R2] = redDc * randomFloat(0.94f, 1.06f);
    base_[IR2] = irDc * randomFloat(0.94f, 1.06f);

    base_[H1] = clampFloat(
      s.humidity + randomFloat(-5.0f, 5.0f), 15.0f, 100.0f
    );

    base_[VBAT] = clampFloat(
      base_[VBAT] + randomFloat(-25.0f, 25.0f), 3300.0f, 4200.0f
    );

    heartRate_ = clampFloat(
      s.heartRate + randomFloat(-5.0f, 5.0f), 45.0f, 190.0f
    );

    spo2_ = clampFloat(
      s.spo2 + randomFloat(-1.0f, 1.0f), 70.0f, 100.0f
    );

    randomized_ = true;
    manual_ = false;
  }

  void setBase(uint8_t channel, float value) {
    if (channel >= CHANNEL_COUNT) return;

    base_[channel] = clampFloat(
      value,
      CHANNELS[channel].minValue,
      CHANNELS[channel].maxValue
    );

    manual_ = true;
    randomized_ = false;
  }

  float base(uint8_t channel) const { return base_[channel]; }
  float value(uint8_t channel) const { return live_[channel]; }

  uint8_t scenarioId() const { return scenarioId_; }

  const char* scenarioName() const {
    if (randomized_) return "Randomized";
    if (manual_) return "Custom";
    return scenario_.name;
  }

  float heartRate() const { return heartRate_; }
  float spo2() const { return spo2_; }

  uint8_t flags() const {
    return FLAG_SIMULATED |
      (manual_ ? FLAG_MANUAL : 0) |
      (scenario_.emergency ? FLAG_EMERGENCY : 0) |
      (randomized_ ? FLAG_RANDOMIZED : 0);
  }

  void sample(uint32_t now) {
    float dt = static_cast<float>(now - lastSampleMs_) * 0.001f;
    lastSampleMs_ = now;

    if (dt <= 0.0f || dt > 0.5f) dt = 0.04f;

    phase_ += dt * (heartRate_ / 60.0f);
    phase_ -= floorf(phase_);

    for (uint8_t i = 0; i < CHANNEL_COUNT; ++i) {
      const float sigma = (i <= TEMP_T4) ? 0.06f :
                          (i <= P4) ? 18.0f :
                          (i <= E6) ? (1.0f + base_[i] * 0.025f) :
                          (i <= IR2) ? (base_[i] * 0.002f) :
                          (i == H1) ? 0.25f : 1.5f;

      noise_[i] = 0.82f * noise_[i] +
        0.572f * gaussianNoise() * sigma * scenario_.noiseScale;
    }

    for (uint8_t i = TEMP_T1; i <= TEMP_T4; ++i) {
      live_[i] = base_[i] + noise_[i];
    }

    for (uint8_t i = P1; i <= P4; ++i) {
      live_[i] = base_[i] + noise_[i] +
        45.0f * sinf(phase_ * 6.2831853f);
    }

    for (uint8_t i = E1; i <= E6; ++i) {
      const float tremor = 1.0f + 0.12f *
        sinf(6.2831853f * 9.5f * (now * 0.001f) + i);

      live_[i] = base_[i] * tremor + noise_[i];
    }

    const float pulse = sinf(phase_ * 6.2831853f);
    const float spo2Ratio =
      clampFloat((110.0f - spo2_) / 25.0f, 0.25f, 1.6f);
    const float ac = 0.012f * scenario_.ppgScale;

    live_[R1] =
      base_[R1] * (1.0f + ac * spo2Ratio * pulse) + noise_[R1];
    live_[IR1] =
      base_[IR1] * (1.0f + ac * pulse) + noise_[IR1];
    live_[R2] =
      base_[R2] * (1.0f + ac * spo2Ratio * pulse) + noise_[R2];
    live_[IR2] =
      base_[IR2] * (1.0f + ac * pulse) + noise_[IR2];

    live_[H1] = base_[H1] + noise_[H1];

    if (now - lastBatteryMs_ >= BATTERY_PERIOD_MS) {
      lastBatteryMs_ = now;
      base_[VBAT] = clampFloat(
        base_[VBAT] - randomFloat(0.0f, 0.8f),
        3300.0f, 4200.0f
      );
    }

    live_[VBAT] = base_[VBAT];

    for (uint8_t i = 0; i < CHANNEL_COUNT; ++i) {
      live_[i] = clampFloat(
        live_[i],
        CHANNELS[i].minValue,
        CHANNELS[i].maxValue
      );
    }
  }

private:
  void generateBaselines() {
    const Scenario& s = scenario_;

    heartRate_ = s.heartRate;
    spo2_ = s.spo2;

    for (uint8_t i = 0; i < 4; ++i) {
      base_[TEMP_T1 + i] =
        s.skinTemp + (static_cast<float>(i) - 1.5f) * 0.2f;
      base_[P1 + i] = s.pressurePa - i * 18.0f;
    }

    for (uint8_t i = 0; i < 6; ++i) {
      base_[E1 + i] =
        (scenarioId_ == RESTING ? 12.0f : 80.0f) *
        s.emgScale * randomFloat(0.8f, 1.2f);
    }

    base_[R1] = 65000.0f;
    base_[IR1] = 105000.0f;
    base_[R2] = 63500.0f;
    base_[IR2] = 102000.0f;
    base_[H1] = s.humidity;

    if (base_[VBAT] < 3300.0f) {
      base_[VBAT] = 4100.0f;
    }

    memset(noise_, 0, sizeof(noise_));

    for (uint8_t i = 0; i < CHANNEL_COUNT; ++i) {
      live_[i] = base_[i];
    }
  }

  Scenario scenario_ = SCENARIOS[RESTING];
  float base_[CHANNEL_COUNT] = {};
  float live_[CHANNEL_COUNT] = {};
  float noise_[CHANNEL_COUNT] = {};

  float heartRate_ = 68.0f;
  float spo2_ = 98.0f;
  float phase_ = 0.0f;

  uint32_t lastSampleMs_ = 0;
  uint32_t lastBatteryMs_ = 0;

  uint8_t scenarioId_ = RESTING;
  bool manual_ = false;
  bool randomized_ = false;
};

// =============================================================================
// Telemetry protocol: CCSDS Space Packet + ASTRO-ARM payload + CRC
// =============================================================================

enum SensorCategory : uint8_t {
  CAT_TMP117      = 0x1,
  CAT_BMP390      = 0x2,
  CAT_ADS1298     = 0x3,
  CAT_MAX86141    = 0x4,
  CAT_SHT45       = 0x5,
  CAT_BQ27441     = 0x6,
  CAT_DEVICE_INFO = 0xF
};

constexpr size_t CCSDS_HEADER_SIZE = 6;
constexpr size_t CCSDS_CRC_SIZE = 2;
constexpr size_t MAX_PAYLOAD_SIZE = 32;
constexpr size_t MAX_PACKET_SIZE =
  CCSDS_HEADER_SIZE + MAX_PAYLOAD_SIZE + CCSDS_CRC_SIZE;

class TelemetryProtocol {
public:
  size_t buildSensorPacket(
    uint8_t category,
    uint8_t scenario,
    uint8_t flags,
    const SensorManager& sensors,
    uint8_t* output,
    size_t capacity
  ) {
    uint8_t payload[MAX_PAYLOAD_SIZE];
    uint8_t* p = payload;

    if (!appendTimestamp(p, payload, sizeof(payload))) {
      return 0;
    }

    p = putU8(p, scenario);
    p = putU8(p, flags);

    switch (category) {
      case CAT_TMP117:
        for (uint8_t i = 0; i < 4; ++i) {
          p = putU16(p, toU16(sensors.value(TEMP_T1 + i), 100.0f));
        }
        break;

      case CAT_BMP390:
        for (uint8_t i = 0; i < 4; ++i) {
          p = putU32(p, toU32(sensors.value(P1 + i), 100.0f));
        }
        break;

      case CAT_ADS1298:
        for (uint8_t i = 0; i < 6; ++i) {
          p = putU16(p, toU16(sensors.value(E1 + i), 1.0f));
        }
        break;

      case CAT_MAX86141:
        for (uint8_t i = 0; i < 4; ++i) {
          p = putU32(p, toU32(sensors.value(R1 + i), 1.0f));
        }
        break;

      case CAT_SHT45:
        p = putU16(p, toU16(sensors.value(H1), 100.0f));
        break;

      case CAT_BQ27441:
        p = putU16(p, toU16(sensors.value(VBAT), 1.0f));
        break;

      default:
        return 0;
    }

    const size_t payloadLength =
      static_cast<size_t>(p - payload);

    return buildPacket(
      category, payload, payloadLength, output, capacity
    );
  }

  size_t buildInfoPacket(uint8_t* output, size_t capacity) {
    uint8_t payload[MAX_PAYLOAD_SIZE] = {};
    uint8_t* p = payload;

    if (!appendTimestamp(p, payload, sizeof(payload))) {
      return 0;
    }

    p = putU8(p, DEVICE_ID_U8);

    strncpy(
      reinterpret_cast<char*>(p),
      DEVICE_NAME,
      DEVICE_NAME_LEN
    );

    p += DEVICE_NAME_LEN;

    return buildPacket(
      CAT_DEVICE_INFO,
      payload,
      static_cast<size_t>(p - payload),
      output,
      capacity
    );
  }

  uint16_t lastSequence() const {
    return lastSequence_;
  }

private:
  bool appendTimestamp(
    uint8_t*& p,
    const uint8_t* buffer,
    size_t capacity
  ) {
    const size_t used = static_cast<size_t>(p - buffer);
    if (used > capacity || capacity - used < CcsdsTimeCode::SIZE) {
      return false;
    }

    if (!CcsdsTimeCode::write(p, capacity - used)) {
      return false;
    }

    p += CcsdsTimeCode::SIZE;
    return true;
  }

  size_t buildPacket(
    uint8_t category,
    const uint8_t* payload,
    size_t payloadLength,
    uint8_t* output,
    size_t capacity
  ) {
    if (!payload || !output ||
        payloadLength == 0 ||
        payloadLength > MAX_PAYLOAD_SIZE) {
      return 0;
    }

    const size_t total =
      CCSDS_HEADER_SIZE + payloadLength + CCSDS_CRC_SIZE;

    if (capacity < total) return 0;

    const uint16_t apid = static_cast<uint16_t>(
      ((DEVICE_ID_U8 & 0x7F) << 4) |
      (category & 0x0F)
    );

    uint16_t sequence = sequence_[category & 0x0F];
    sequence_[category & 0x0F] =
      static_cast<uint16_t>((sequence + 1) & 0x3FFF);

    lastSequence_ = sequence;

    const uint16_t word0 = apid;
    const uint16_t word1 = static_cast<uint16_t>(0xC000 | sequence);

    // CCSDS Packet Data Length = Packet Data Field octets - 1.
    // The ASTRO-ARM CRC is included in the Packet Data Field.
    const uint16_t word2 = static_cast<uint16_t>(
      payloadLength + CCSDS_CRC_SIZE - 1
    );

    uint8_t* p = output;
    p = putU16(p, word0);
    p = putU16(p, word1);
    p = putU16(p, word2);

    memcpy(p, payload, payloadLength);
    p += payloadLength;

    // ASTRO-ARM CRC-16/CCITT-FALSE:
    // init=0xFFFF, poly=0x1021, refin=false, refout=false, xorout=0.
    // Covers the 6-byte primary header and payload, excluding CRC itself.
    const uint16_t crc = crc16CcittFalse(
      output,
      CCSDS_HEADER_SIZE + payloadLength
    );

    p = putU16(p, crc);
    return static_cast<size_t>(p - output);
  }

  uint16_t sequence_[16] = {};
  uint16_t lastSequence_ = 0;
};

// =============================================================================
// Transport abstraction
// =============================================================================

class TransportInterface {
public:
  virtual ~TransportInterface() = default;

  virtual void begin() = 0;
  virtual void service(uint32_t now) = 0;
  virtual bool connected() const = 0;
  virtual bool send(const uint8_t* data, size_t length) = 0;
  virtual bool consumeConnectionEvent() = 0;
};

// =============================================================================
// Wi-Fi UDP transport
// =============================================================================

class WifiTransport : public TransportInterface {
public:
  void begin() override {
    char hostname[20];

    snprintf(
      hostname,
      sizeof(hostname),
      "astro-%03u",
      DEVICE_ID_U8
    );

    WiFi.persistent(false);
    WiFi.setHostname(hostname);
    WiFi.mode(WIFI_STA);
    WiFi.setSleep(false);
    WiFi.setAutoReconnect(true);
    WiFi.begin(WIFI_SSID, WIFI_PASS);

    // NTP supplies UTC. CcsdsTimeCode converts UTC to TAI.
    configTime(0, 0, "pool.ntp.org", "time.nist.gov");

    lastAttemptMs_ = millis();
  }

  void service(uint32_t now) override {
    if (WiFi.status() == WL_CONNECTED) {
      if (!udpStarted_) {
        const uint16_t sourcePort =
          UDP_SOURCE_PORT_BASE + DEVICE_ID_U8;

        udpStarted_ = (udp_.begin(sourcePort) != 0);

        if (udpStarted_) {
          justConnected_ = true;
        }
      }

      return;
    }

    if (udpStarted_) {
      udp_.stop();
      udpStarted_ = false;
    }

    if (now - lastAttemptMs_ >=
        WIFI_RETRY_MS + DEVICE_ID_U8 * 20U) {
      lastAttemptMs_ = now;
      WiFi.reconnect();
    }
  }

  bool connected() const override {
    return WiFi.status() == WL_CONNECTED && udpStarted_;
  }

  bool consumeConnectionEvent() override {
    const bool result = justConnected_;
    justConnected_ = false;
    return result;
  }

  bool send(const uint8_t* data, size_t length) override {
    if (!connected() || !data || length == 0) {
      return false;
    }

    if (!udp_.beginPacket(DEST_IP, UDP_DEST_PORT)) {
      return false;
    }

    const size_t written = udp_.write(data, length);
    const bool packetSent = (udp_.endPacket() == 1);

    return written == length && packetSent;
  }

private:
  WiFiUDP udp_;
  uint32_t lastAttemptMs_ = 0;
  bool udpStarted_ = false;
  bool justConnected_ = false;
};

// =============================================================================
// TFT UI
// =============================================================================

#define RGB565(r,g,b) ((uint16_t)((((r)&0xF8)<<8) | (((g)&0xFC)<<3) | ((b)>>3)))

constexpr uint16_t COLOR_BG = TFT_BLACK;
constexpr uint16_t COLOR_HEADER = RGB565(0, 160, 200);
constexpr uint16_t COLOR_SELECTED = RGB565(0, 65, 105);
constexpr uint16_t COLOR_TEXT = RGB565(215, 225, 235);
constexpr uint16_t COLOR_DIM = RGB565(130, 145, 155);
constexpr uint16_t COLOR_OK = RGB565(70, 220, 120);

class DisplayUI {
public:
  enum Screen : uint8_t {
    MENU,
    PRESETS,
    PARAMETERS,
    LIVE
  };

  DisplayUI(TFT_eSPI& display, SensorManager& sensors)
    : tft_(display), sensors_(sensors) {}

  void begin() {
    width_ = tft_.width();
    height_ = tft_.height();
    dirty_ = true;
  }

  bool editing() const {
    return editing_;
  }

  void next() {
    const uint8_t count = itemCount();
    if (!count) return;

    selection_[screen_] =
      static_cast<uint8_t>((selection_[screen_] + 1) % count);

    dirty_ = true;
  }

  void previous() {
    const uint8_t count = itemCount();
    if (!count) return;

    selection_[screen_] =
      static_cast<uint8_t>((selection_[screen_] + count - 1) % count);

    dirty_ = true;
  }

  void back() {
    if (editing_) {
      editing_ = false;
    } else if (screen_ != MENU) {
      screen_ = MENU;
    } else {
      return;
    }

    dirty_ = true;
  }

  void select() {
    switch (screen_) {
      case MENU:
        if (selection_[MENU] == 0) {
          screen_ = PRESETS;
          selection_[PRESETS] = sensors_.scenarioId();
        } else if (selection_[MENU] == 1) {
          screen_ = PARAMETERS;
        } else if (selection_[MENU] == 2) {
          sensors_.randomize();
          showToast("Values randomized");
        } else if (selection_[MENU] == 3) {
          screen_ = LIVE;
        } else {
          txEnabled_ = !txEnabled_;
          showToast(txEnabled_ ? "Telemetry TX ON" : "Telemetry TX OFF");
        }
        break;

      case PRESETS:
        sensors_.applyScenario(selection_[PRESETS]);
        screen_ = MENU;
        showToast("Scenario applied");
        break;

      case PARAMETERS:
        editing_ = true;
        break;

      default:
        break;
    }

    dirty_ = true;
  }

  void adjust(int direction, uint32_t now) {
    if (screen_ != PARAMETERS || !editing_) return;

    if (now - lastAdjustMs_ < 350) {
      if (adjustmentStreak_ < 20) ++adjustmentStreak_;
    } else {
      adjustmentStreak_ = 0;
    }

    lastAdjustMs_ = now;

    const float multiplier =
      adjustmentStreak_ < 3 ? 1.0f :
      adjustmentStreak_ < 6 ? 3.0f :
      adjustmentStreak_ < 10 ? 10.0f : 30.0f;

    const uint8_t channel = selection_[PARAMETERS];

    sensors_.setBase(
      channel,
      sensors_.base(channel) +
      direction * CHANNELS[channel].step * multiplier
    );

    dirty_ = true;
  }

  bool txEnabled() const {
    return txEnabled_;
  }

  void update(uint32_t now) {
    if (toastUntil_ && static_cast<int32_t>(now - toastUntil_) >= 0) {
      toastUntil_ = 0;
      dirty_ = true;
    }

    if (dirty_) {
      dirty_ = false;
      drawScreen();
      lastLiveMs_ = now;
      return;
    }

    if (toastUntil_) return;

    if (screen_ == LIVE && now - lastLiveMs_ >= 250) {
      lastLiveMs_ = now;
      drawLiveValues();
    }
  }

private:
  uint8_t itemCount() const {
    switch (screen_) {
      case MENU: return 5;
      case PRESETS: return SCENARIO_COUNT;
      case PARAMETERS: return CHANNEL_COUNT;
      default: return 0;
    }
  }

  void showToast(const char* message) {
    strncpy(toast_, message, sizeof(toast_) - 1);
    toast_[sizeof(toast_) - 1] = '\0';
    toastUntil_ = millis() + 1000;
  }

  void drawHeader(const char* title) {
    tft_.fillRect(0, 0, width_, 25, COLOR_HEADER);
    tft_.setTextColor(TFT_BLACK, COLOR_HEADER);
    tft_.setTextDatum(ML_DATUM);
    tft_.drawString(title, 6, 12, 2);
  }

  void drawFooter(const char* text) {
    tft_.fillRect(0, height_ - 15, width_, 15, COLOR_BG);
    tft_.setTextColor(COLOR_DIM, COLOR_BG);
    tft_.setTextDatum(TL_DATUM);
    tft_.drawString(text, 4, height_ - 12, 1);
  }

  void drawRow(
    uint8_t row,
    const char* left,
    const char* right,
    bool selected
  ) {
    const int y = 30 + row * 24;
    const uint16_t bg = selected ? COLOR_SELECTED : COLOR_BG;

    tft_.fillRect(0, y, width_, 22, bg);
    tft_.setTextColor(selected ? TFT_WHITE : COLOR_TEXT, bg);
    tft_.setTextDatum(ML_DATUM);
    tft_.drawString(left, 8, y + 11, 2);

    tft_.setTextDatum(MR_DATUM);
    tft_.drawString(right, width_ - 8, y + 11, 2);
  }

  void drawScreen() {
    tft_.fillScreen(COLOR_BG);

    char value[32];

    if (screen_ == MENU) {
      drawHeader(DEVICE_NAME);

      const char* items[] = {
        "Preset Selection",
        "Parameters",
        "Randomize Values",
        "Live Telemetry",
        "Telemetry TX"
      };

      for (uint8_t i = 0; i < 5; ++i) {
        const char* right = "";

        if (i == 0) right = sensors_.scenarioName();
        if (i == 4) right = txEnabled_ ? "ON" : "OFF";

        drawRow(i, items[i], right, i == selection_[MENU]);
      }

      drawFooter("A: next / hold: prev   B: select / hold: back");
    } else if (screen_ == PRESETS) {
      drawHeader("Operational Presets");

      for (uint8_t i = 0; i < SCENARIO_COUNT; ++i) {
        drawRow(
          i,
          SCENARIOS[i].name,
          i == sensors_.scenarioId() ? "ACTIVE" : "",
          i == selection_[PRESETS]
        );
      }

      drawFooter("A: next   B: apply   hold B: back");
    } else if (screen_ == PARAMETERS) {
      drawHeader(editing_ ? "Parameters - EDIT" : "Raw sensor parameters");

      const uint8_t top =
        selection_[PARAMETERS] > 7
          ? selection_[PARAMETERS] - 7
          : 0;

      for (uint8_t row = 0;
           row < 8 && top + row < CHANNEL_COUNT;
           ++row) {
        const uint8_t channel = top + row;

        snprintf(
          value,
          sizeof(value),
          "%.*f %s",
          CHANNELS[channel].decimals,
          sensors_.base(channel),
          CHANNELS[channel].unit
        );

        drawRow(
          row,
          CHANNELS[channel].name,
          value,
          channel == selection_[PARAMETERS]
        );
      }

      drawFooter(
        editing_
          ? "A: +   B: -   hold: finish"
          : "A: next   B: edit   hold B: back"
      );
    } else {
      drawHeader("Live Sensor Data");
      drawLiveStatic();
      drawLiveValues();
    }

    if (toastUntil_) {
      tft_.fillRoundRect(
        20, height_ / 2 - 20, width_ - 40, 40, 6, COLOR_OK
      );

      tft_.setTextColor(TFT_BLACK, COLOR_OK);
      tft_.setTextDatum(MC_DATUM);
      tft_.drawString(toast_, width_ / 2, height_ / 2, 2);
    }
  }

  void drawLiveStatic() {
    tft_.setTextColor(COLOR_DIM, COLOR_BG);
    tft_.setTextDatum(TL_DATUM);

    for (uint8_t i = 0; i < CHANNEL_COUNT; ++i) {
      const uint8_t column = i / 10;
      const uint8_t row = i % 10;
      const int x = column ? 164 : 4;
      const int y = 30 + row * 18;

      tft_.drawString(CHANNELS[i].name, x, y, 1);
      tft_.drawString(CHANNELS[i].unit, x + 32, y, 1);
    }
  }

  void drawLiveValues() {
    char value[20];

    tft_.setTextColor(TFT_WHITE, COLOR_BG);
    tft_.setTextDatum(TR_DATUM);
    tft_.setTextPadding(65);

    for (uint8_t i = 0; i < CHANNEL_COUNT; ++i) {
      const uint8_t column = i / 10;
      const uint8_t row = i % 10;
      const int x = column ? 160 : 0;
      const int y = 30 + row * 18;

      snprintf(
        value,
        sizeof(value),
        "%.*f",
        CHANNELS[i].decimals,
        sensors_.value(i)
      );

      tft_.drawString(value, x + 112, y, 1);
    }

    tft_.setTextPadding(0);

    char status[64];

    snprintf(
      status,
      sizeof(status),
      "%s  HR %.0f  SpO2 %.1f",
      sensors_.scenarioName(),
      sensors_.heartRate(),
      sensors_.spo2()
    );

    tft_.setTextColor(COLOR_OK, COLOR_BG);
    tft_.setTextDatum(TL_DATUM);
    tft_.drawString(status, 4, height_ - 28, 1);
  }

  TFT_eSPI& tft_;
  SensorManager& sensors_;

  Screen screen_ = MENU;
  uint8_t selection_[4] = {};

  bool editing_ = false;
  bool dirty_ = true;
  bool txEnabled_ = true;

  uint16_t width_ = 320;
  uint16_t height_ = 240;

  uint8_t adjustmentStreak_ = 0;
  uint32_t lastAdjustMs_ = 0;
  uint32_t lastLiveMs_ = 0;
  uint32_t toastUntil_ = 0;

  char toast_[32] = {};
};

// =============================================================================
// Application scheduler
// =============================================================================

class Application {
public:
  Application(
    TFT_eSPI& display,
    SensorManager& sensors,
    TelemetryProtocol& telemetry,
    TransportInterface& transport,
    DisplayUI& ui,
    Button& buttonA,
    Button& buttonB
  )
    : display_(display),
      sensors_(sensors),
      telemetry_(telemetry),
      transport_(transport),
      ui_(ui),
      buttonA_(buttonA),
      buttonB_(buttonB) {}

  void begin() {
    Serial.begin(115200);

    display_.init();
    display_.setRotation(1);
    display_.fillScreen(COLOR_BG);

    buttonA_.setDebTimeout(25);
    buttonB_.setDebTimeout(25);
    buttonA_.setHoldTimeout(500);
    buttonB_.setHoldTimeout(500);

    sensors_.begin();
    transport_.begin();
    ui_.begin();

    const uint32_t now = millis();
    lastSampleMs_ = now - SAMPLE_PERIOD_MS;
    lastInfoMs_ = now - INFO_PERIOD_MS;

    Serial.printf(
      "ASTRO-ARM simulator, device %u, mode=%s\n",
      DEVICE_ID_U8,
      SIMULATION_MODE ? "SIMULATION" : "HARDWARE"
    );
  }

  void update() {
    const uint32_t now = millis();

    processButtons(now);
    transport_.service(now);

    if (ui_.txEnabled() && transport_.connected()) {
      if (transport_.consumeConnectionEvent() ||
          now - lastInfoMs_ >= INFO_PERIOD_MS) {
        lastInfoMs_ = now;

        const size_t infoLength =
          telemetry_.buildInfoPacket(txBuffer_, sizeof(txBuffer_));

        if (infoLength) {
          transport_.send(txBuffer_, infoLength);
        }
      }
    }

    if (now - lastSampleMs_ >= SAMPLE_PERIOD_MS) {
      lastSampleMs_ = now;
      sensors_.sample(now);

      if (ui_.txEnabled() && transport_.connected()) {
        transmitAllSensorPackets();
      }
    }

    if (ui_.txEnabled() && transport_.connected() &&
        now - lastBatteryPacketMs_ >= BATTERY_PERIOD_MS) {
      lastBatteryPacketMs_ = now;
      transmitCategory(CAT_BQ27441);
    }

    ui_.update(now);
  }

private:
  void processButtons(uint32_t now) {
    buttonA_.tick();
    buttonB_.tick();

    if (ui_.editing()) {
      if (buttonA_.click()) ui_.adjust(+1, now);
      if (buttonB_.click()) ui_.adjust(-1, now);

      if (buttonA_.hold() || buttonB_.hold()) {
        ui_.back();
      }
    } else {
      if (buttonA_.click()) ui_.next();

      if (buttonA_.hold() || buttonA_.step()) {
        ui_.previous();
      }

      if (buttonB_.click()) ui_.select();
      if (buttonB_.hold()) ui_.back();
    }
  }

  void transmitCategory(uint8_t category) {
    const size_t length = telemetry_.buildSensorPacket(
      category,
      sensors_.scenarioId(),
      sensors_.flags(),
      sensors_,
      txBuffer_,
      sizeof(txBuffer_)
    );

    if (length) {
      transport_.send(txBuffer_, length);
    }
  }

  void transmitAllSensorPackets() {
    transmitCategory(CAT_TMP117);
    transmitCategory(CAT_BMP390);
    transmitCategory(CAT_ADS1298);
    transmitCategory(CAT_MAX86141);
    transmitCategory(CAT_SHT45);
  }

  TFT_eSPI& display_;
  SensorManager& sensors_;
  TelemetryProtocol& telemetry_;
  TransportInterface& transport_;
  DisplayUI& ui_;

  Button& buttonA_;
  Button& buttonB_;

  uint8_t txBuffer_[MAX_PACKET_SIZE] = {};

  uint32_t lastSampleMs_ = 0;
  uint32_t lastInfoMs_ = 0;
  uint32_t lastBatteryPacketMs_ = 0;
};

// =============================================================================
// Global objects and Arduino entry points
// =============================================================================

static TFT_eSPI tft;
static SensorManager sensors;
static TelemetryProtocol telemetry;
static WifiTransport wifiTransport;
static TransportInterface& transport = wifiTransport;
static DisplayUI ui(tft, sensors);

static Button buttonA(BTN_A_PIN);
static Button buttonB(BTN_B_PIN);

static Application app(
  tft,
  sensors,
  telemetry,
  transport,
  ui,
  buttonA,
  buttonB
);

void setup() {
  app.begin();
}

void loop() {
  app.update();
}