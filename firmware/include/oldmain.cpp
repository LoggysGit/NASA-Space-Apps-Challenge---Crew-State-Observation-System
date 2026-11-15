#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <TFT_eSPI.h>
#include <EncButton.h>
#include <esp_system.h>

#include <string.h>
#include <time.h>
#include <sys/time.h>

#include "config.h"

#include "stats.h"

static Stats stats;

// True once per `periodMs`; the first call always passes.
static bool rateLimit(uint32_t& last, uint32_t now, uint32_t periodMs) {
  if (last != 0 && now - last < periodMs) return false;
  last = now;
  return true;
}

// =============================================================================
// FILE: util.h  (random, math, big-endian serialization, CRC)
// =============================================================================

static uint32_t rngState = 2463534242UL;

// xorshift32: much cheaper than esp_random() and good enough for noise.
static uint32_t rngNext() {
  rngState ^= rngState << 13;
  rngState ^= rngState >> 17;
  rngState ^= rngState << 5;
  return rngState;
}

static float randomFloat(float lo, float hi) {
  return lo + (hi - lo) * ((float)rngNext() * 2.3283064e-10f);
}

// Sum of 6 uniform values -> approximately normal, range -3..+3.
static float gaussianNoise() {
  float sum = 0.0f;
  for (uint8_t i = 0; i < 6; i++) sum += randomFloat(0.0f, 1.0f);
  return sum - 3.0f;
}

static float clampFloat(float v, float lo, float hi) {
  return v < lo ? lo : (v > hi ? hi : v);
}

static uint16_t toU16(float value, float scale) {
  long q = lroundf(value * scale);
  if (q < 0) q = 0;
  if (q > 65535) q = 65535;
  return (uint16_t)q;
}

static uint32_t toU32(float value, float scale) {
  double q = round((double)value * scale);
  if (q < 0.0) q = 0.0;
  if (q > 4294967295.0) q = 4294967295.0;
  return (uint32_t)q;
}

static uint8_t* putU8(uint8_t* p, uint8_t v) {
  *p++ = v;
  return p;
}

static uint8_t* putU16(uint8_t* p, uint16_t v) {
  *p++ = (uint8_t)(v >> 8);
  *p++ = (uint8_t)v;
  return p;
}

static uint8_t* putU32(uint8_t* p, uint32_t v) {
  p = putU16(p, (uint16_t)(v >> 16));
  return putU16(p, (uint16_t)v);
}

// CRC-16/CCITT-FALSE: init 0xFFFF, poly 0x1021, no reflection, xorout 0.
static uint16_t crc16(const uint8_t* data, size_t len) {
  uint16_t crc = 0xFFFF;
  while (len--) {
    crc ^= (uint16_t)(*data++) << 8;
    for (uint8_t bit = 0; bit < 8; bit++) {
      crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
    }
  }
  return crc;
}

// =============================================================================
// FILE: timecode.h  (CCSDS CUC Level 1)
// =============================================================================

static bool timeSynced() {
  timeval tv;
  if (gettimeofday(&tv, nullptr) != 0) return false;
  return tv.tv_sec >= MIN_VALID_UNIX_TIME;
}

// Writes 7 bytes: P-field + 4 bytes coarse (s since 1958 TAI) + 2 bytes fine.
static bool timeCode(uint8_t* out) {
  timeval tv;
  if (gettimeofday(&tv, nullptr) != 0 || tv.tv_sec < MIN_VALID_UNIX_TIME) return false;

  uint64_t coarse = (uint64_t)tv.tv_sec + UNIX_TO_CCSDS_EPOCH_S + TAI_MINUS_UTC_S;
  uint32_t fine = (uint32_t)(((uint64_t)tv.tv_usec * 65536ULL) / 1000000ULL);

  out[0] = 0x1E;  // CUC, 1958 epoch, 4 coarse + 2 fine octets
  uint8_t* p = out + 1;
  p = putU32(p, (uint32_t)coarse);
  putU16(p, (uint16_t)fine);
  return true;
}

// Logs NTP state changes and (rate-limited) a warning while the clock is not set.
static void timeService(uint32_t now) {
  static bool wasSynced = false;
  static uint32_t lastWarn = 0;

  const bool synced = timeSynced();
  if (synced != wasSynced) {
    wasSynced = synced;
    if (synced) {
      LOGI("TIME", "clock synchronized (unix=%ld)", (long)time(nullptr));
    } else {
      LOGW("TIME", "clock lost");
    }
  }
  if (!synced && WiFi.status() == WL_CONNECTED && rateLimit(lastWarn, now, 5000)) {
    LOGW("TIME", "waiting for NTP - telemetry packets are NOT sent until the clock is set");
  }
}

// =============================================================================
// FILE: channels.h  (channel table and scenarios)
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
  {"t1",  "C",      20.0f,     42.0f,   0.1f, 1},
  {"t2",  "C",      20.0f,     42.0f,   0.1f, 1},
  {"t3",  "C",      20.0f,     42.0f,   0.1f, 1},
  {"t4",  "C",      20.0f,     42.0f,   0.1f, 1},
  {"p1",  "Pa",  30000.0f, 125000.0f,  10.0f, 0},
  {"p2",  "Pa",  30000.0f, 125000.0f,  10.0f, 0},
  {"p3",  "Pa",  30000.0f, 125000.0f,  10.0f, 0},
  {"p4",  "Pa",  30000.0f, 125000.0f,  10.0f, 0},
  {"e1",  "uV",      0.0f,   5000.0f,   2.0f, 0},
  {"e2",  "uV",      0.0f,   5000.0f,   2.0f, 0},
  {"e3",  "uV",      0.0f,   5000.0f,   2.0f, 0},
  {"e4",  "uV",      0.0f,   5000.0f,   2.0f, 0},
  {"e5",  "uV",      0.0f,   5000.0f,   2.0f, 0},
  {"e6",  "uV",      0.0f,   5000.0f,   2.0f, 0},
  {"r1",  "cnt",     0.0f, 524287.0f, 100.0f, 0},
  {"ir1", "cnt",     0.0f, 524287.0f, 100.0f, 0},
  {"r2",  "cnt",     0.0f, 524287.0f, 100.0f, 0},
  {"ir2", "cnt",     0.0f, 524287.0f, 100.0f, 0},
  {"h1",  "%RH",     0.0f,    100.0f,   1.0f, 1},
  {"vBat", "mV",  3000.0f,   4400.0f,  10.0f, 0}
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
  {"Resting Normal",        68, 98, 101325, 1.0f, 1.0f, 45, 33.5f, 1.0f, false},
  {"High G-Force Load",    112, 95, 101500, 2.0f, 0.9f, 62, 34.5f, 1.8f, false},
  {"Extreme Exertion",     168, 95, 101200, 4.0f, 1.3f, 88, 36.0f, 2.2f, false},
  {"Hypoxia Simulation",    98, 82,  90000, 1.2f, 0.6f, 52, 32.0f, 1.4f, false},
  {"Thermal Stress",       118, 97, 101000, 1.5f, 1.4f, 96, 38.0f, 1.6f, false},
  {"Muscle Fatigue",        96, 97, 101300, 2.8f, 1.0f, 66, 34.2f, 1.3f, false},
  {"Emergency Malfunction", 148, 74,  85000, 4.5f, 0.4f, 97, 31.5f, 3.5f, true}
};

#define FLAG_SIMULATED   (1 << 0)
#define FLAG_MANUAL      (1 << 1)
#define FLAG_EMERGENCY   (1 << 2)
#define FLAG_RANDOMIZED  (1 << 3)

// =============================================================================
// FILE: sensors.h  (simulated sensor model)
// =============================================================================

static Scenario sc = SCENARIOS[RESTING];
static float baseVal[CHANNEL_COUNT];
static float liveVal[CHANNEL_COUNT];
static float noiseVal[CHANNEL_COUNT];

static float heartRate = 68.0f;
static float spo2 = 98.0f;
static float pulsePhase = 0.0f;     // 0..1, one turn per heartbeat
static float tremorPhase = 0.0f;    // radians, wrapped (avoids float precision loss)

static uint32_t lastSampleMs = 0;
static uint32_t lastBatteryMs = 0;

static uint8_t scenarioId = RESTING;
static bool manualMode = false;
static bool randomizedMode = false;

static void sensorsGenerateBaselines() {
  heartRate = sc.heartRate;
  spo2 = sc.spo2;

  for (uint8_t i = 0; i < 4; i++) {
    baseVal[TEMP_T1 + i] = sc.skinTemp + ((float)i - 1.5f) * 0.2f;
    baseVal[P1 + i] = sc.pressurePa - i * 18.0f;
  }
  for (uint8_t i = 0; i < 6; i++) {
    baseVal[E1 + i] = (scenarioId == RESTING ? 12.0f : 80.0f) * sc.emgScale * randomFloat(0.8f, 1.2f);
  }

  baseVal[R1] = 65000.0f;
  baseVal[IR1] = 105000.0f;
  baseVal[R2] = 63500.0f;
  baseVal[IR2] = 102000.0f;
  baseVal[H1] = sc.humidity;
  if (baseVal[VBAT] < 3300.0f) baseVal[VBAT] = 4100.0f;

  memset(noiseVal, 0, sizeof(noiseVal));
  memcpy(liveVal, baseVal, sizeof(liveVal));
}

static void sensorsApplyScenario(uint8_t id) {
  if (id >= SCENARIO_COUNT) id = RESTING;
  scenarioId = id;
  sc = SCENARIOS[id];
  manualMode = false;
  randomizedMode = false;
  sensorsGenerateBaselines();
  LOGI("SENS", "scenario applied: %s (HR %.0f, SpO2 %.0f)", sc.name, sc.heartRate, sc.spo2);
}

static void sensorsRandomize() {
  const float tempSpread = (scenarioId == RESTING) ? 0.45f : 0.8f;
  const float pressureSpread = (scenarioId == RESTING) ? 180.0f : 450.0f;

  for (uint8_t i = 0; i < 4; i++) {
    baseVal[TEMP_T1 + i] = clampFloat(
      sc.skinTemp + ((float)i - 1.5f) * 0.25f + randomFloat(-tempSpread, tempSpread), 28.0f, 40.0f);
    baseVal[P1 + i] = clampFloat(
      sc.pressurePa + randomFloat(-pressureSpread, pressureSpread), 30000.0f, 125000.0f);
  }

  const float emgLo = scenarioId == RESTING ? 5.0f : 10.0f;
  const float emgHi = scenarioId == RESTING ? 35.0f : 1200.0f;
  for (uint8_t i = 0; i < 6; i++) {
    baseVal[E1 + i] = clampFloat(randomFloat(emgLo, emgHi) * sc.emgScale, 0.0f, 5000.0f);
  }

  const float redDc = randomFloat(45000.0f, 95000.0f);
  const float irDc = randomFloat(70000.0f, 145000.0f);
  baseVal[R1] = redDc;
  baseVal[IR1] = irDc;
  baseVal[R2] = redDc * randomFloat(0.94f, 1.06f);
  baseVal[IR2] = irDc * randomFloat(0.94f, 1.06f);

  baseVal[H1] = clampFloat(sc.humidity + randomFloat(-5.0f, 5.0f), 15.0f, 100.0f);
  baseVal[VBAT] = clampFloat(baseVal[VBAT] + randomFloat(-25.0f, 25.0f), 3300.0f, 4200.0f);

  heartRate = clampFloat(sc.heartRate + randomFloat(-5.0f, 5.0f), 45.0f, 190.0f);
  spo2 = clampFloat(sc.spo2 + randomFloat(-1.0f, 1.0f), 70.0f, 100.0f);

  randomizedMode = true;
  manualMode = false;
  LOGI("SENS", "values randomized: HR %.0f, SpO2 %.1f", heartRate, spo2);
}

static void sensorsSetBase(uint8_t ch, float value) {
  if (ch >= CHANNEL_COUNT) return;
  baseVal[ch] = clampFloat(value, CHANNELS[ch].minValue, CHANNELS[ch].maxValue);
  manualMode = true;
  randomizedMode = false;
}

static const char* sensorsScenarioName() {
  if (randomizedMode) return "Randomized";
  if (manualMode) return "Custom";
  return sc.name;
}

static uint8_t sensorsFlags() {
  return FLAG_SIMULATED |
         (manualMode ? FLAG_MANUAL : 0) |
         (sc.emergency ? FLAG_EMERGENCY : 0) |
         (randomizedMode ? FLAG_RANDOMIZED : 0);
}

static void sensorsBegin() {
  baseVal[VBAT] = 4100.0f;
  sensorsApplyScenario(RESTING);
  lastSampleMs = millis();
  lastBatteryMs = lastSampleMs;
}

static void sensorsSample(uint32_t now) {
  float dt = (float)(now - lastSampleMs) * 0.001f;
  lastSampleMs = now;
  if (dt <= 0.0f || dt > 0.5f) dt = SAMPLE_PERIOD_MS * 0.001f;

  pulsePhase += dt * (heartRate / 60.0f);
  pulsePhase -= floorf(pulsePhase);

  tremorPhase += dt * TWO_PI_F * 9.5f;
  if (tremorPhase > TWO_PI_F) tremorPhase -= TWO_PI_F;

  // Colored noise per channel (AR(1) filter on gaussian noise).
  for (uint8_t i = 0; i < CHANNEL_COUNT; i++) {
    const float sigma = (i <= TEMP_T4) ? 0.06f :
                        (i <= P4)      ? 18.0f :
                        (i <= E6)      ? (1.0f + baseVal[i] * 0.025f) :
                        (i <= IR2)     ? (baseVal[i] * 0.002f) :
                        (i == H1)      ? 0.25f : 1.5f;
    noiseVal[i] = 0.82f * noiseVal[i] + 0.572f * gaussianNoise() * sigma * sc.noiseScale;
  }

  const float pulse = sinf(pulsePhase * TWO_PI_F);   // computed once per sample

  for (uint8_t i = TEMP_T1; i <= TEMP_T4; i++) {
    liveVal[i] = baseVal[i] + noiseVal[i];
  }
  for (uint8_t i = P1; i <= P4; i++) {
    liveVal[i] = baseVal[i] + noiseVal[i] + 45.0f * pulse;
  }
  for (uint8_t i = E1; i <= E6; i++) {
    liveVal[i] = baseVal[i] * (1.0f + 0.12f * sinf(tremorPhase + i)) + noiseVal[i];
  }

  const float spo2Ratio = clampFloat((110.0f - spo2) / 25.0f, 0.25f, 1.6f);
  const float ac = 0.012f * sc.ppgScale;
  liveVal[R1]  = baseVal[R1]  * (1.0f + ac * spo2Ratio * pulse) + noiseVal[R1];
  liveVal[IR1] = baseVal[IR1] * (1.0f + ac * pulse)             + noiseVal[IR1];
  liveVal[R2]  = baseVal[R2]  * (1.0f + ac * spo2Ratio * pulse) + noiseVal[R2];
  liveVal[IR2] = baseVal[IR2] * (1.0f + ac * pulse)             + noiseVal[IR2];

  liveVal[H1] = baseVal[H1] + noiseVal[H1];

  if (now - lastBatteryMs >= BATTERY_PERIOD_MS) {
    lastBatteryMs = now;
    baseVal[VBAT] = clampFloat(baseVal[VBAT] - randomFloat(0.0f, 0.8f), 3300.0f, 4200.0f);
  }
  liveVal[VBAT] = baseVal[VBAT];

  for (uint8_t i = 0; i < CHANNEL_COUNT; i++) {
    liveVal[i] = clampFloat(liveVal[i], CHANNELS[i].minValue, CHANNELS[i].maxValue);
  }
}

// =============================================================================
// FILE: protocol.h  (CCSDS space packet + ASTRO-ARM payload + CRC)
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

#define CCSDS_HEADER_SIZE  6
#define CCSDS_CRC_SIZE     2
#define MAX_PAYLOAD_SIZE   32
#define MAX_PACKET_SIZE    (CCSDS_HEADER_SIZE + MAX_PAYLOAD_SIZE + CCSDS_CRC_SIZE)

static uint16_t seqCounter[16];

static const char* categoryName(uint8_t cat) {
  switch (cat) {
    case CAT_TMP117:      return "TMP117";
    case CAT_BMP390:      return "BMP390";
    case CAT_ADS1298:     return "ADS1298";
    case CAT_MAX86141:    return "MAX86141";
    case CAT_SHT45:       return "SHT45";
    case CAT_BQ27441:     return "BQ27441";
    case CAT_DEVICE_INFO: return "INFO";
    default:              return "?";
  }
}

// Wraps payload into: 6-byte primary header + payload + CRC16.
static size_t protoBuildPacket(uint8_t cat, const uint8_t* payload, size_t payloadLen,
                               uint8_t* out, size_t cap) {
  if (payloadLen == 0 || payloadLen > MAX_PAYLOAD_SIZE) {
    LOGE("PROTO", "%s: bad payload length %u", categoryName(cat), (unsigned)payloadLen);
    return 0;
  }

  const size_t total = CCSDS_HEADER_SIZE + payloadLen + CCSDS_CRC_SIZE;
  if (cap < total) {
    LOGE("PROTO", "%s: buffer too small (%u < %u)", categoryName(cat), (unsigned)cap, (unsigned)total);
    return 0;
  }

  const uint16_t apid = (uint16_t)(((DEVICE_ID_U8 & 0x7F) << 4) | (cat & 0x0F));
  const uint16_t seq = seqCounter[cat & 0x0F];
  seqCounter[cat & 0x0F] = (uint16_t)((seq + 1) & 0x3FFF);

  uint8_t* p = out;
  p = putU16(p, apid);
  p = putU16(p, (uint16_t)(0xC000 | seq));                          // unsegmented + sequence
  p = putU16(p, (uint16_t)(payloadLen + CCSDS_CRC_SIZE - 1));       // data length - 1

  memcpy(p, payload, payloadLen);
  p += payloadLen;

  p = putU16(p, crc16(out, CCSDS_HEADER_SIZE + payloadLen));        // header + payload
  return (size_t)(p - out);
}

// `ts` is a 7-byte time code shared by all packets of one sample cycle.
static size_t protoBuildSensor(uint8_t cat, const uint8_t* ts, uint8_t* out, size_t cap) {
  uint8_t payload[MAX_PAYLOAD_SIZE];
  uint8_t* p = payload;

  memcpy(p, ts, TIME_CODE_SIZE);
  p += TIME_CODE_SIZE;
  p = putU8(p, scenarioId);
  p = putU8(p, sensorsFlags());

  switch (cat) {
    case CAT_TMP117:
      for (uint8_t i = 0; i < 4; i++) p = putU16(p, toU16(liveVal[TEMP_T1 + i], 100.0f));
      break;
    case CAT_BMP390:
      for (uint8_t i = 0; i < 4; i++) p = putU32(p, toU32(liveVal[P1 + i], 100.0f));
      break;
    case CAT_ADS1298:
      for (uint8_t i = 0; i < 6; i++) p = putU16(p, toU16(liveVal[E1 + i], 1.0f));
      break;
    case CAT_MAX86141:
      for (uint8_t i = 0; i < 4; i++) p = putU32(p, toU32(liveVal[R1 + i], 1.0f));
      break;
    case CAT_SHT45:
      p = putU16(p, toU16(liveVal[H1], 100.0f));
      break;
    case CAT_BQ27441:
      p = putU16(p, toU16(liveVal[VBAT], 1.0f));
      break;
    default:
      LOGE("PROTO", "unknown category 0x%X", cat);
      return 0;
  }

  return protoBuildPacket(cat, payload, (size_t)(p - payload), out, cap);
}

static size_t protoBuildInfo(const uint8_t* ts, uint8_t* out, size_t cap) {
  uint8_t payload[MAX_PAYLOAD_SIZE] = {};
  uint8_t* p = payload;

  memcpy(p, ts, TIME_CODE_SIZE);
  p += TIME_CODE_SIZE;
  p = putU8(p, DEVICE_ID_U8);
  strncpy((char*)p, DEVICE_NAME, DEVICE_NAME_LEN);
  p += DEVICE_NAME_LEN;

  return protoBuildPacket(CAT_DEVICE_INFO, payload, (size_t)(p - payload), out, cap);
}

// =============================================================================
// FILE: wifi_transport.h  (Wi-Fi + UDP)
// =============================================================================

static WiFiUDP udp;
static bool udpStarted = false;
static bool justConnected = false;
static uint32_t lastWifiAttemptMs = 0;

enum SendResult : uint8_t { SEND_OK = 0, SEND_NOT_CONNECTED, SEND_BEGIN_FAIL, SEND_SHORT_WRITE, SEND_END_FAIL };
static const char* const SEND_RESULT_NAMES[] = {"ok", "not connected", "beginPacket", "short write", "endPacket"};

static const char* wifiStatusName(wl_status_t s) {
  switch (s) {
    case WL_IDLE_STATUS:     return "IDLE";
    case WL_NO_SSID_AVAIL:   return "NO_SSID";
    case WL_SCAN_COMPLETED:  return "SCAN_DONE";
    case WL_CONNECTED:       return "CONNECTED";
    case WL_CONNECT_FAILED:  return "CONNECT_FAILED";
    case WL_CONNECTION_LOST: return "CONNECTION_LOST";
    case WL_DISCONNECTED:    return "DISCONNECTED";
    default:                 return "?";
  }
}

// Runs in the Wi-Fi event task: keep it short.
static void wifiEvent(arduino_event_id_t event, arduino_event_info_t info) {
  switch (event) {
    case ARDUINO_EVENT_WIFI_STA_START:
      LOGI("WIFI", "station started");
      break;
    case ARDUINO_EVENT_WIFI_STA_CONNECTED:
      LOGI("WIFI", "associated with AP, channel %u", (unsigned)info.wifi_sta_connected.channel);
      break;
    case ARDUINO_EVENT_WIFI_STA_GOT_IP:
      stats.wifiConnects++;
      LOGI("WIFI", "got IP %s", IPAddress(info.got_ip.ip_info.ip.addr).toString().c_str());
      break;
    case ARDUINO_EVENT_WIFI_STA_DISCONNECTED:
      stats.wifiDisconnects++;
      LOGW("WIFI", "disconnected, reason %u", (unsigned)info.wifi_sta_disconnected.reason);
      break;
    default:
      break;
  }
}

static void wifiBegin() {
  char hostname[20];
  snprintf(hostname, sizeof(hostname), "astro-%03u", DEVICE_ID_U8);

  WiFi.onEvent(wifiEvent);
  WiFi.persistent(false);
  WiFi.setHostname(hostname);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  // NTP gives UTC; timeCode() converts it to TAI.
  configTime(0, 0, "pool.ntp.org", "time.nist.gov");

  lastWifiAttemptMs = millis();
  LOGI("WIFI", "connecting to \"%s\" as %s", WIFI_SSID, hostname);
}

static void wifiService(uint32_t now) {
  if (WiFi.status() == WL_CONNECTED) {
    if (!udpStarted) {
      const uint16_t port = UDP_SOURCE_PORT_BASE + DEVICE_ID_U8;
      udpStarted = (udp.begin(port) != 0);
      if (udpStarted) {
        justConnected = true;
        LOGI("UDP", "socket open on local port %u, destination %s:%u",
             port, DEST_IP.toString().c_str(), UDP_DEST_PORT);
      } else {
        LOGE("UDP", "udp.begin(%u) failed", port);
      }
    }
    return;
  }

  if (udpStarted) {
    udp.stop();
    udpStarted = false;
    LOGW("UDP", "socket closed (Wi-Fi down)");
  }

  if (now - lastWifiAttemptMs >= WIFI_RETRY_MS + DEVICE_ID_U8 * 20U) {
    lastWifiAttemptMs = now;
    LOGW("WIFI", "retrying, status=%s", wifiStatusName(WiFi.status()));
    WiFi.reconnect();
  }
}

static bool wifiConnected() {
  return udpStarted && WiFi.status() == WL_CONNECTED;
}

static bool wifiConsumeConnectionEvent() {
  const bool r = justConnected;
  justConnected = false;
  return r;
}

static SendResult wifiSend(const uint8_t* data, size_t len) {
  if (!wifiConnected() || !data || len == 0) return SEND_NOT_CONNECTED;
  if (!udp.beginPacket(DEST_IP, UDP_DEST_PORT)) return SEND_BEGIN_FAIL;
  if (udp.write(data, len) != len) return SEND_SHORT_WRITE;
  if (udp.endPacket() != 1) return SEND_END_FAIL;
  return SEND_OK;
}

// =============================================================================
// FILE: display_ui.h  (TFT menu)
// =============================================================================

#define RGB565(r, g, b) ((uint16_t)((((r) & 0xF8) << 8) | (((g) & 0xFC) << 3) | ((b) >> 3)))

#define COLOR_BG        TFT_BLACK
#define COLOR_HEADER    RGB565(0, 160, 200)
#define COLOR_SELECTED  RGB565(0, 65, 105)
#define COLOR_TEXT      RGB565(215, 225, 235)
#define COLOR_DIM       RGB565(130, 145, 155)
#define COLOR_OK        RGB565(70, 220, 120)

#define ROW_Y0      30
#define ROW_HEIGHT  24
#define PARAM_ROWS  8
#define MENU_ITEM_COUNT 5

enum Screen : uint8_t { SCREEN_MENU, SCREEN_PRESETS, SCREEN_PARAMETERS, SCREEN_LIVE };

// Redraw state: nothing / whole screen / only the listed rows.
#define UI_CLEAN 0
#define UI_FULL  1
#define UI_ROWS  2

static TFT_eSPI tft;

static const char* const MENU_ITEMS[MENU_ITEM_COUNT] = {
  "Preset Selection", "Parameters", "Randomize Values", "Live Telemetry", "Telemetry TX"
};

static uint8_t uiScreen = SCREEN_MENU;
static uint8_t uiSelection[4] = {};
static bool uiEditing = false;
static bool uiTxEnabled = true;

static uint8_t uiDirty = UI_FULL;
static uint8_t uiRows[4];
static uint8_t uiRowCount = 0;

static uint16_t uiWidth = 320;
static uint16_t uiHeight = 240;

static uint8_t uiStreak = 0;
static uint32_t uiLastAdjustMs = 0;
static uint32_t uiLastLiveMs = 0;
static uint32_t uiToastUntil = 0;
static char uiToast[32] = {};

static char liveCache[CHANNEL_COUNT][16];
static char liveStatusCache[48];

static const char* screenName(uint8_t s) {
  switch (s) {
    case SCREEN_MENU:       return "menu";
    case SCREEN_PRESETS:    return "presets";
    case SCREEN_PARAMETERS: return "parameters";
    default:                return "live";
  }
}

static void uiMarkFull() {
  uiDirty = UI_FULL;
}

static void uiMarkRow(uint8_t item) {
  if (uiDirty == UI_FULL) return;
  if (uiToastUntil) { uiDirty = UI_FULL; return; }   // never paint rows over a toast
  if (uiDirty == UI_CLEAN) { uiDirty = UI_ROWS; uiRowCount = 0; }
  if (uiRowCount < sizeof(uiRows)) uiRows[uiRowCount++] = item;
  else uiDirty = UI_FULL;
}

static uint8_t uiItemCount() {
  switch (uiScreen) {
    case SCREEN_MENU:       return MENU_ITEM_COUNT;
    case SCREEN_PRESETS:    return SCENARIO_COUNT;
    case SCREEN_PARAMETERS: return CHANNEL_COUNT;
    default:                return 0;
  }
}

static uint8_t uiParamTop() {
  const uint8_t sel = uiSelection[SCREEN_PARAMETERS];
  return sel > PARAM_ROWS - 1 ? sel - (PARAM_ROWS - 1) : 0;
}

static void uiShowToast(const char* message) {
  strncpy(uiToast, message, sizeof(uiToast) - 1);
  uiToast[sizeof(uiToast) - 1] = '\0';
  uiToastUntil = millis() + 1000;
  LOGI("UI", "toast: %s", message);
}

static void uiMove(int8_t dir) {
  const uint8_t count = uiItemCount();
  if (!count) return;

  const uint8_t oldSel = uiSelection[uiScreen];
  const uint8_t oldTop = (uiScreen == SCREEN_PARAMETERS) ? uiParamTop() : 0;

  const uint8_t newSel = (uint8_t)((oldSel + count + dir) % count);
  uiSelection[uiScreen] = newSel;

  const uint8_t newTop = (uiScreen == SCREEN_PARAMETERS) ? uiParamTop() : 0;
  if (oldTop != newTop) {
    uiMarkFull();            // list scrolled
  } else {
    uiMarkRow(oldSel);
    uiMarkRow(newSel);
  }
}

static void uiNext()     { uiMove(+1); }
static void uiPrevious() { uiMove(-1); }

static void uiBack() {
  if (uiEditing) {
    uiEditing = false;
  } else if (uiScreen != SCREEN_MENU) {
    uiScreen = SCREEN_MENU;
  } else {
    return;
  }
  LOGI("UI", "back -> %s%s", screenName(uiScreen), uiEditing ? " (edit)" : "");
  uiMarkFull();
}

static void uiSelect() {
  switch (uiScreen) {
    case SCREEN_MENU:
      switch (uiSelection[SCREEN_MENU]) {
        case 0:
          uiScreen = SCREEN_PRESETS;
          uiSelection[SCREEN_PRESETS] = scenarioId;
          break;
        case 1:
          uiScreen = SCREEN_PARAMETERS;
          break;
        case 2:
          sensorsRandomize();
          uiShowToast("Values randomized");
          break;
        case 3:
          uiScreen = SCREEN_LIVE;
          memset(liveCache, 0, sizeof(liveCache));
          liveStatusCache[0] = '\0';
          break;
        default:
          uiTxEnabled = !uiTxEnabled;
          uiShowToast(uiTxEnabled ? "Telemetry TX ON" : "Telemetry TX OFF");
          break;
      }
      break;

    case SCREEN_PRESETS:
      sensorsApplyScenario(uiSelection[SCREEN_PRESETS]);
      uiScreen = SCREEN_MENU;
      uiShowToast("Scenario applied");
      break;

    case SCREEN_PARAMETERS:
      uiEditing = true;
      break;

    default:
      break;
  }
  LOGI("UI", "select -> %s%s", screenName(uiScreen), uiEditing ? " (edit)" : "");
  uiMarkFull();
}

static void uiAdjust(int8_t direction, uint32_t now) {
  if (uiScreen != SCREEN_PARAMETERS || !uiEditing) return;

  if (now - uiLastAdjustMs < 350) {
    if (uiStreak < 20) uiStreak++;
  } else {
    uiStreak = 0;
  }
  uiLastAdjustMs = now;

  const float mult = uiStreak < 3 ? 1.0f : uiStreak < 6 ? 3.0f : uiStreak < 10 ? 10.0f : 30.0f;
  const uint8_t ch = uiSelection[SCREEN_PARAMETERS];

  sensorsSetBase(ch, baseVal[ch] + direction * CHANNELS[ch].step * mult);
  LOGD("UI", "adjust %s -> %.2f %s (x%.0f)", CHANNELS[ch].name, baseVal[ch], CHANNELS[ch].unit, mult);
  uiMarkRow(ch);
}

// ---- drawing ---------------------------------------------------------------

static void drawHeader(const char* title) {
  tft.fillRect(0, 0, uiWidth, 25, COLOR_HEADER);
  tft.setTextColor(TFT_BLACK, COLOR_HEADER);
  tft.setTextDatum(ML_DATUM);
  tft.drawString(title, 6, 12, 2);
}

static void drawFooter(const char* text) {
  tft.fillRect(0, uiHeight - 15, uiWidth, 15, COLOR_BG);
  tft.setTextColor(COLOR_DIM, COLOR_BG);
  tft.setTextDatum(TL_DATUM);
  tft.drawString(text, 4, uiHeight - 12, 1);
}

static void drawRow(uint8_t row, const char* left, const char* right, bool selected) {
  const int y = ROW_Y0 + row * ROW_HEIGHT;
  const uint16_t bg = selected ? COLOR_SELECTED : COLOR_BG;

  tft.fillRect(0, y, uiWidth, 22, bg);
  tft.setTextColor(selected ? TFT_WHITE : COLOR_TEXT, bg);
  tft.setTextDatum(ML_DATUM);
  tft.drawString(left, 8, y + 11, 2);
  tft.setTextDatum(MR_DATUM);
  tft.drawString(right, uiWidth - 8, y + 11, 2);
}

// Draws one list item of the current screen (no-op if it is not visible).
static void uiDrawItem(uint8_t item) {
  char value[32];
  const bool selected = (item == uiSelection[uiScreen]);

  switch (uiScreen) {
    case SCREEN_MENU: {
      const char* right = "";
      if (item == 0) right = sensorsScenarioName();
      if (item == 4) right = uiTxEnabled ? "ON" : "OFF";
      drawRow(item, MENU_ITEMS[item], right, selected);
      break;
    }
    case SCREEN_PRESETS:
      drawRow(item, SCENARIOS[item].name, item == scenarioId ? "ACTIVE" : "", selected);
      break;

    case SCREEN_PARAMETERS: {
      const uint8_t top = uiParamTop();
      if (item < top || item >= top + PARAM_ROWS) return;
      snprintf(value, sizeof(value), "%.*f %s", CHANNELS[item].decimals, baseVal[item], CHANNELS[item].unit);
      drawRow(item - top, CHANNELS[item].name, value, selected);
      break;
    }
    default:
      break;
  }
}

static void drawLiveStatic() {
  tft.setTextColor(COLOR_DIM, COLOR_BG);
  tft.setTextDatum(TL_DATUM);
  for (uint8_t i = 0; i < CHANNEL_COUNT; i++) {
    const int x = (i / 10) ? 164 : 4;
    const int y = 30 + (i % 10) * 18;
    tft.drawString(CHANNELS[i].name, x, y, 1);
    tft.drawString(CHANNELS[i].unit, x + 32, y, 1);
  }
}

// Only values whose text changed are sent to the display (SPI is the bottleneck).
static void drawLiveValues() {
  char value[16];

  tft.setTextColor(TFT_WHITE, COLOR_BG);
  tft.setTextDatum(TR_DATUM);
  tft.setTextPadding(65);
  for (uint8_t i = 0; i < CHANNEL_COUNT; i++) {
    snprintf(value, sizeof(value), "%.*f", CHANNELS[i].decimals, liveVal[i]);
    if (strcmp(value, liveCache[i]) == 0) continue;
    strcpy(liveCache[i], value);

    const int x = (i / 10) ? 160 : 0;
    const int y = 30 + (i % 10) * 18;
    tft.drawString(value, x + 112, y, 1);
  }

  char status[48];
  snprintf(status, sizeof(status), "%s  HR %.0f  SpO2 %.1f", sensorsScenarioName(), heartRate, spo2);
  if (strcmp(status, liveStatusCache) != 0) {
    strcpy(liveStatusCache, status);
    tft.setTextPadding(uiWidth - 8);
    tft.setTextColor(COLOR_OK, COLOR_BG);
    tft.setTextDatum(TL_DATUM);
    tft.drawString(status, 4, uiHeight - 28, 1);
  }
  tft.setTextPadding(0);
}

static void uiDrawFull() {
  tft.fillScreen(COLOR_BG);

  switch (uiScreen) {
    case SCREEN_MENU:
      drawHeader(DEVICE_NAME);
      for (uint8_t i = 0; i < MENU_ITEM_COUNT; i++) uiDrawItem(i);
      drawFooter("A: next / hold: prev   B: select / hold: back");
      break;

    case SCREEN_PRESETS:
      drawHeader("Operational Presets");
      for (uint8_t i = 0; i < SCENARIO_COUNT; i++) uiDrawItem(i);
      drawFooter("A: next   B: apply   hold B: back");
      break;

    case SCREEN_PARAMETERS: {
      drawHeader(uiEditing ? "Parameters - EDIT" : "Raw sensor parameters");
      const uint8_t top = uiParamTop();
      for (uint8_t row = 0; row < PARAM_ROWS && top + row < CHANNEL_COUNT; row++) uiDrawItem(top + row);
      drawFooter(uiEditing ? "A: +   B: -   hold: finish" : "A: next   B: edit   hold B: back");
      break;
    }
    default:
      drawHeader("Live Sensor Data");
      drawLiveStatic();
      memset(liveCache, 0, sizeof(liveCache));
      liveStatusCache[0] = '\0';
      drawLiveValues();
      break;
  }

  if (uiToastUntil) {
    tft.fillRoundRect(20, uiHeight / 2 - 20, uiWidth - 40, 40, 6, COLOR_OK);
    tft.setTextColor(TFT_BLACK, COLOR_OK);
    tft.setTextDatum(MC_DATUM);
    tft.drawString(uiToast, uiWidth / 2, uiHeight / 2, 2);
  }
}

static void uiBegin() {
  uiWidth = tft.width();
  uiHeight = tft.height();
  uiDirty = UI_FULL;
}

static void uiUpdate(uint32_t now) {
  if (uiToastUntil && (int32_t)(now - uiToastUntil) >= 0) {
    uiToastUntil = 0;
    uiMarkFull();
  }

  if (uiDirty == UI_FULL) {
    uiDirty = UI_CLEAN;
    uiDrawFull();
    uiLastLiveMs = now;
    return;
  }

  if (uiDirty == UI_ROWS) {
    uiDirty = UI_CLEAN;
    for (uint8_t i = 0; i < uiRowCount; i++) uiDrawItem(uiRows[i]);
    uiRowCount = 0;
    return;
  }

  if (uiScreen == SCREEN_LIVE && !uiToastUntil && now - uiLastLiveMs >= 250) {
    uiLastLiveMs = now;
    drawLiveValues();
  }
}

// =============================================================================
// FILE: app.h  (buttons, scheduler, debug report)
// =============================================================================

static Button buttonA(BTN_A_PIN);
static Button buttonB(BTN_B_PIN);

static uint8_t txBuffer[MAX_PACKET_SIZE];
static uint32_t lastInfoMs = 0;
static uint32_t lastBatteryPacketMs = 0;
static uint32_t lastHexMs[16] = {};

static bool txReady() {
  return uiTxEnabled && wifiConnected();
}

static void txNoteFailure(SendResult r) {
  static uint32_t lastLog = 0;
  stats.sendFail++;
  if (rateLimit(lastLog, millis(), 2000)) {
    LOGW("TX", "send failed: %s (total failures %lu)", SEND_RESULT_NAMES[r], UL(stats.sendFail));
  }
}

#if DEBUG_LEVEL >= 4
static void txHexDump(uint8_t cat, const uint8_t* data, size_t len) {
  if (!rateLimit(lastHexMs[cat & 0x0F], millis(), DEBUG_HEX_MS)) return;
  char line[MAX_PACKET_SIZE * 3 + 1];
  char* p = line;
  for (size_t i = 0; i < len; i++) p += sprintf(p, "%02X ", data[i]);
  LOGD("HEX", "%-8s len=%u: %s", categoryName(cat), (unsigned)len, line);
}
#endif

// Builds and sends one packet of `cat`; `ts` is the shared time code (or nullptr for INFO).
static void txSend(uint8_t cat, const uint8_t* ts) {
  const size_t len = (cat == CAT_DEVICE_INFO)
    ? protoBuildInfo(ts, txBuffer, sizeof(txBuffer))
    : protoBuildSensor(cat, ts, txBuffer, sizeof(txBuffer));

  if (!len) {
    stats.buildFail++;
    return;
  }

  const SendResult r = wifiSend(txBuffer, len);
  if (r == SEND_OK) {
    stats.sent[cat & 0x0F]++;
#if DEBUG_LEVEL >= 4
    txHexDump(cat, txBuffer, len);
#endif
  } else {
    txNoteFailure(r);
  }
}

// One time stamp for the whole cycle; if the clock is not set nothing is sent.
static void txSendWithTime(const uint8_t* cats, uint8_t count) {
  static uint32_t lastWarn = 0;
  uint8_t ts[TIME_CODE_SIZE];

  if (!timeCode(ts)) {
    stats.noTimeDrops += count;
    if (rateLimit(lastWarn, millis(), 5000)) {
      LOGW("TX", "clock not synchronized - dropped %lu packets so far", UL(stats.noTimeDrops));
    }
    return;
  }
  for (uint8_t i = 0; i < count; i++) txSend(cats[i], ts);
}

static void processButtons(uint32_t now) {
  buttonA.tick();
  buttonB.tick();

  if (uiEditing) {
    if (buttonA.click()) { LOGD("BTN", "A click (edit +)"); uiAdjust(+1, now); }
    if (buttonB.click()) { LOGD("BTN", "B click (edit -)"); uiAdjust(-1, now); }
    if (buttonA.hold() || buttonB.hold()) { LOGD("BTN", "hold (finish edit)"); uiBack(); }
    return;
  }

  if (buttonA.click()) { LOGD("BTN", "A click"); uiNext(); }
  if (buttonA.hold() || buttonA.step()) { LOGD("BTN", "A hold/step"); uiPrevious(); }
  if (buttonB.click()) { LOGD("BTN", "B click"); uiSelect(); }
  if (buttonB.hold()) { LOGD("BTN", "B hold"); uiBack(); }
}

static void debugReport(uint32_t now) {
#if DEBUG_LEVEL >= 3
  if (now - stats.lastReportMs < DEBUG_STATS_MS) return;

  uint32_t total = 0;
  for (uint8_t i = 0; i < 16; i++) total += stats.sent[i];
  const uint32_t elapsed = now - stats.lastReportMs;
  const uint32_t pps = elapsed ? (uint32_t)(((uint64_t)(total - stats.lastTotalSent) * 1000ULL) / elapsed) : 0;

  const wl_status_t ws = WiFi.status();
  LOGI("STAT", "up=%lus heap=%lu (min %lu) loopMax=%luus",
       UL(now / 1000), UL(ESP.getFreeHeap()), UL(ESP.getMinFreeHeap()), UL(stats.loopMaxUs));
  LOGI("STAT", "wifi=%s rssi=%d ip=%s time=%s tx=%s",
       wifiStatusName(ws), ws == WL_CONNECTED ? WiFi.RSSI() : 0,
       WiFi.localIP().toString().c_str(), timeSynced() ? "synced" : "NOT synced",
       uiTxEnabled ? "on" : "off");
  LOGI("STAT", "sent %lu pkt/s | TMP=%lu BMP=%lu ADS=%lu MAX=%lu SHT=%lu BQ=%lu INFO=%lu",
       UL(pps), UL(stats.sent[CAT_TMP117]), UL(stats.sent[CAT_BMP390]), UL(stats.sent[CAT_ADS1298]),
       UL(stats.sent[CAT_MAX86141]), UL(stats.sent[CAT_SHT45]), UL(stats.sent[CAT_BQ27441]),
       UL(stats.sent[CAT_DEVICE_INFO]));
  LOGI("STAT", "fail: send=%lu build=%lu noTime=%lu | wifi up=%lu down=%lu",
       UL(stats.sendFail), UL(stats.buildFail), UL(stats.noTimeDrops),
       UL(stats.wifiConnects), UL(stats.wifiDisconnects));
  LOGI("STAT", "scenario=%s HR=%.0f SpO2=%.1f flags=0x%02X",
       sensorsScenarioName(), heartRate, spo2, sensorsFlags());

  stats.lastTotalSent = total;
  stats.lastReportMs = now;
  stats.loopMaxUs = 0;
#else
  (void)now;
#endif
}

static void appBegin() {
  Serial.begin(115200);
  rngState = esp_random() | 1;      // non-zero seed for xorshift

  LOGI("BOOT", "ASTRO-ARM simulator: device %u \"%s\"", DEVICE_ID_U8, DEVICE_NAME);
  LOGI("BOOT", "chip %s @ %u MHz, reset reason %d, free heap %lu",
       ESP.getChipModel(), (unsigned)ESP.getCpuFreqMHz(), (int)esp_reset_reason(), UL(ESP.getFreeHeap()));
  LOGI("BOOT", "dest %s:%u, sample %u ms, debug level %d",
       DEST_IP.toString().c_str(), UDP_DEST_PORT, SAMPLE_PERIOD_MS, DEBUG_LEVEL);

  tft.init();
  tft.setRotation(1);
  tft.fillScreen(COLOR_BG);

  buttonA.setDebTimeout(25);
  buttonB.setDebTimeout(25);
  buttonA.setHoldTimeout(500);
  buttonB.setHoldTimeout(500);

  sensorsBegin();
  wifiBegin();
  uiBegin();

  const uint32_t now = millis();
  lastInfoMs = now - INFO_PERIOD_MS;      // send INFO right away
  lastBatteryPacketMs = now;
  stats.lastReportMs = now;
  lastSampleMs = now - SAMPLE_PERIOD_MS;  // first sample right away
}

static void appUpdate() {
  const uint32_t loopStartUs = micros();
  const uint32_t now = millis();

  processButtons(now);
  wifiService(now);
  timeService(now);

  // INFO packet: on every (re)connect and every INFO_PERIOD_MS.
  const bool connectEvent = wifiConsumeConnectionEvent();
  if (txReady() && (connectEvent || now - lastInfoMs >= INFO_PERIOD_MS)) {
    lastInfoMs = now;
    uint8_t ts[TIME_CODE_SIZE];
    if (timeCode(ts)) txSend(CAT_DEVICE_INFO, ts);
    else stats.noTimeDrops++;
  }

  // Sensor packets at 25 Hz.
  if (now - lastSampleMs >= SAMPLE_PERIOD_MS) {
    sensorsSample(now);
    if (txReady()) {
      static const uint8_t cats[] = {CAT_TMP117, CAT_BMP390, CAT_ADS1298, CAT_MAX86141, CAT_SHT45};
      txSendWithTime(cats, sizeof(cats));
    }
  }

  // Battery packet at 1 Hz.
  if (txReady() && now - lastBatteryPacketMs >= BATTERY_PERIOD_MS) {
    lastBatteryPacketMs = now;
    static const uint8_t cats[] = {CAT_BQ27441};
    txSendWithTime(cats, 1);
  }

  uiUpdate(now);
  debugReport(now);

  const uint32_t loopUs = micros() - loopStartUs;
  if (loopUs > stats.loopMaxUs) stats.loopMaxUs = loopUs;
}

// =============================================================================
// Arduino entry points
// =============================================================================

void setup() {
  appBegin();
}

void loop() {
  appUpdate();
}