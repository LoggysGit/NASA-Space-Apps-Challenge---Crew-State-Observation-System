/**
 * ============================================================================
 *  SPACE BIOMETRIC WEARABLE  -  forearm cuff, PROTOTYPE / SIMULATOR FIRMWARE
 * ============================================================================
 *  Target   : ESP32 (Arduino framework), colour TFT (TFT_eSPI), 2 buttons
 *             (EncButton), Wi-Fi UDP telemetry to a central Linux host.
 *  Purpose  : Generates realistic *simulated* sensor data for 19 channels,
 *             lets the operator pick scenario presets / edit single channels /
 *             randomise values, and streams everything as CCSDS-inspired
 *             binary space packets.
 *
 *  NETWORK MODEL (many ESPs, one Linux host)
 *  ----------------------------------------------------------------------------
 *  The Linux computer is the Wi-Fi Access Point (hostapd) and the only
 *  telemetry sink. Every ESP is a plain station (peer) of that AP; ESPs never
 *  talk to each other. To keep many devices from colliding:
 *    - each device has its own DEVICE_ID (0..127) and DEVICE_NAME (see CONFIG);
 *    - the ID is carried in every packet's APID, so the host demultiplexes by
 *      APID >> 4 (one UDP port for all devices, no per-device ports needed);
 *    - the DHCP hostname and UDP source port are derived from the ID;
 *    - telemetry is UNICAST to the AP (no subnet broadcast, so devices do not
 *      receive each other's traffic and the AP is not flooded with broadcasts);
 *    - the 25 Hz frame clock, the identity beacon and Wi-Fi reconnect attempts
 *      are staggered by ID so the devices do not transmit in lock-step;
 *    - optional static IP derived from the ID (TM_USE_STATIC_IP) if you do not
 *      want to rely on DHCP leases.
 *  Build one firmware per device, e.g. in platformio.ini:
 *      build_flags = ... -DDEVICE_ID=7 -DDEVICE_NAME=\"unit-07\"
 *
 *  CONTROLS
 *  ----------------------------------------------------------------------------
 *  Lists   : A click = next, A hold = previous (auto-repeat), B click = select,
 *            B hold = back.
 *  Editing : (Parameters -> B on a channel) A click = +, B click = -,
 *            quick repeated clicks accelerate the step, hold either = done.
 *
 *  PACKET FORMAT (all multi-byte fields BIG-ENDIAN, as in CCSDS)
 *  ----------------------------------------------------------------------------
 *  Primary header, 6 bytes:
 *    word0 (bytes 0-1): ver[3]=000 | type[1]=0 | sec_hdr[1]=0 | APID[11]
 *                       APID = (DEVICE_ID[7] << 4) | category[4]
 *    word1 (bytes 2-3): seq_flags[2]=11 | seq_count[14]
 *    word2 (bytes 4-5): packet_data_length = (payload bytes) - 1
 *  Payload (54 bytes with CRC):
 *    off  0  u32  MET, ms since boot
 *    off  4  u8   scenario/preset id
 *    off  5  u8   flags (b0 SIM, b1 MANUAL edit, b2 EMERGENCY, b3 RANDOMIZED)
 *    off  6  u16  t1..t4   0.01 degC
 *    off 14  u16  p1..p4   0.1 mmHg
 *    off 22  u16  e1..e6   uV RMS
 *    off 34  u32  r1, ir1, r2, ir2   raw 18-bit ADC counts (MAX30102 style)
 *    off 50  u16  h1       0.01 %RH
 *    off 52  u16  CRC-16/CCITT-FALSE over header + payload bytes 0..51
 *
 *  DEVICE INFO beacon (category 0xF, sent every TM_INFO_PERIOD_MS), payload:
 *    off  0  u8   DEVICE_ID
 *    off  1  16 B DEVICE_NAME, ASCII, NUL padded
 *    off 17  u16  CRC-16/CCITT-FALSE over header + payload bytes 0..16
 *
 *  PHYSIOLOGICAL DATA NOTE
 *  ----------------------------------------------------------------------------
 *  Scenario values are representative simulation values (resting HR/BP/SpO2,
 *  skin temperature, sEMG RMS ranges, etc.). They are NOT mission-validated
 *  limits. Alert thresholds belong on the host side and must be taken from
 *  NASA-STD-3001 Vol. 2 and the OCHMO technical briefs.
 * ============================================================================
 */

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <TFT_eSPI.h>
#include <EncButton.h>
#include <esp_system.h>
#include <math.h>
#include <string.h>
#include <stdio.h>

// ============================================================================
//  1. CONFIGURATION
// ============================================================================
// ---- Device identity (override per device with -DDEVICE_ID / -DDEVICE_NAME) ----
#ifndef DEVICE_ID
#define DEVICE_ID 1                     // unique per device on the network, 0..127
#endif
#ifndef DEVICE_NAME
#define DEVICE_NAME "unit-01"           // human-readable name, max 16 chars
#endif
#define DEVICE_NAME_MAX 16

static_assert(DEVICE_ID >= 0 && DEVICE_ID <= 127, "DEVICE_ID must be 0..127 (7 bits)");
static_assert(sizeof(DEVICE_NAME) - 1 <= DEVICE_NAME_MAX, "DEVICE_NAME must be at most 16 chars");
static const uint8_t     DEV_ID   = (uint8_t)DEVICE_ID;
static const char* const DEV_NAME = DEVICE_NAME;

// ---- Wi-Fi: this ESP is a station (peer) of the Linux host's access point ----
#define WIFI_SSID "SPACE-LINK-AP"       // SSID of the Linux host's AP (WPA2-PSK)
#define WIFI_PASS "change-me-please"

// Optional static IP = STATIC_NET.(STATIC_HOST_BASE + DEVICE_ID). Default: DHCP.
// With DHCP, give each device a unique hostname (done below) and, if you like,
// a reservation on the Linux side. Keep STATIC_HOST_BASE + 127 <= 254.
#define TM_USE_STATIC_IP 0
#define STATIC_NET        192, 168, 4
#define STATIC_HOST_BASE  100
#define STATIC_GATEWAY    192, 168, 4, 1
#define STATIC_NETMASK    255, 255, 255, 0

// Telemetry destination: UNICAST. 1 = the Wi-Fi gateway (the Linux AP itself),
// 0 = the fixed address below. Broadcast is intentionally not used.
#define TM_DEST_IS_GATEWAY 1
#define TM_DEST_IP 192, 168, 4, 1       // used when TM_DEST_IS_GATEWAY == 0
static const uint16_t TM_UDP_PORT        = 5005;  // host listens here for all devices
static const uint16_t TM_UDP_SRC_PORT_BASE = 40000;  // source port = base + DEVICE_ID

#define TM_APPEND_CRC16 1               // CCSDS-style packet error control
static const uint32_t TM_PERIOD_MS      = 40;     // 25 Hz telemetry frame rate
static const uint32_t TM_INFO_PERIOD_MS = 2000;   // identity beacon period
static const uint32_t WIFI_RETRY_MS     = 8000;   // base reconnect interval

static const uint8_t BTN_A_PIN = 32;    // Next / increase
static const uint8_t BTN_B_PIN = 33;    // Select / decrease (active LOW, pull-up)

// ============================================================================
//  2. SMALL HELPERS
// ============================================================================
static inline float frand(float lo, float hi) {
  return lo + (hi - lo) * ((float)esp_random() / 4294967296.0f);
}
// Approx. N(0,1): sum of 4 uniforms has mean 2 and std 0.57735.
static inline float gauss() {
  float s = 0.0f;
  for (uint8_t i = 0; i < 4; i++) s += frand(0.0f, 1.0f);
  return (s - 2.0f) * 1.7320508f;
}
static inline float clampf(float v, float lo, float hi) {
  return v < lo ? lo : (v > hi ? hi : v);
}
static inline uint16_t q16(float v, float scale) {
  long x = lroundf(v * scale);
  if (x < 0) x = 0;
  if (x > 65535) x = 65535;
  return (uint16_t)x;
}
static inline uint32_t q18(float v) {
  long x = lroundf(v);
  if (x < 0) x = 0;
  if (x > 262143) x = 262143;
  return (uint32_t)x;
}

// ============================================================================
//  3. CHANNEL MODEL
// ============================================================================
enum Channel : uint8_t {
  CH_T1, CH_T2, CH_T3, CH_T4,
  CH_P1, CH_P2, CH_P3, CH_P4,
  CH_E1, CH_E2, CH_E3, CH_E4, CH_E5, CH_E6,
  CH_R1, CH_IR1, CH_R2, CH_IR2,
  CH_H1,
  NUM_CH
};

struct ChannelDef {
  const char* name;
  const char* desc;
  const char* unit;
  float minV, maxV, step;
  uint8_t decimals;
};

static const ChannelDef CH_DEF[NUM_CH] = {
  {"t1", "Skin temp 1",  "C",    20.0f, 45.0f, 0.1f, 1},
  {"t2", "Skin temp 2",  "C",    20.0f, 45.0f, 0.1f, 1},
  {"t3", "Skin temp 3",  "C",    20.0f, 45.0f, 0.1f, 1},
  {"t4", "Skin temp 4",  "C",    20.0f, 45.0f, 0.1f, 1},
  {"p1", "Pressure 1",   "mmHg",  0.0f, 300.0f, 1.0f, 1},
  {"p2", "Pressure 2",   "mmHg",  0.0f, 300.0f, 1.0f, 1},
  {"p3", "Pressure 3",   "mmHg",  0.0f, 300.0f, 1.0f, 1},
  {"p4", "Pressure 4",   "mmHg",  0.0f, 300.0f, 1.0f, 1},
  {"e1", "EMG 1 (RMS)",  "uV",    0.0f, 5000.0f, 2.0f, 0},
  {"e2", "EMG 2 (RMS)",  "uV",    0.0f, 5000.0f, 2.0f, 0},
  {"e3", "EMG 3 (RMS)",  "uV",    0.0f, 5000.0f, 2.0f, 0},
  {"e4", "EMG 4 (RMS)",  "uV",    0.0f, 5000.0f, 2.0f, 0},
  {"e5", "EMG 5 (RMS)",  "uV",    0.0f, 5000.0f, 2.0f, 0},
  {"e6", "EMG 6 (RMS)",  "uV",    0.0f, 5000.0f, 2.0f, 0},
  {"r1", "PPG1 red",     "cnt",   0.0f, 262143.0f, 250.0f, 0},
  {"ir1", "PPG1 IR",     "cnt",   0.0f, 262143.0f, 250.0f, 0},
  {"r2", "PPG2 red",     "cnt",   0.0f, 262143.0f, 250.0f, 0},
  {"ir2", "PPG2 IR",     "cnt",   0.0f, 262143.0f, 250.0f, 0},
  {"h1", "Cuff humidity", "%RH",  0.0f, 100.0f, 1.0f, 1},
};

// Scenario definition. 'base' is in engineering units, channel order above.
// Parameter editing changes the BASELINE; the simulator adds noise, pulsation
// (pressure, PPG), tremor (EMG) and optional glitches on top.
struct Preset {
  const char* name;
  float hr;         // simulated heart rate, bpm (drives pulse waveform)
  float spo2;       // simulated SpO2, % (sets red/IR AC ratio, R = (110-SpO2)/25)
  float pp;         // pulse pressure, mmHg
  float perf;       // IR perfusion index (AC/DC), fraction
  float noise;      // noise multiplier (motion / vibration)
  float tremor;     // 0..1, 9-10 Hz EMG modulation depth
  float glitch;     // per-frame probability of a one-channel spike
  float irregular;  // 0..1, beat-to-beat irregularity
  bool  emergency;
  float base[NUM_CH];
};

static const Preset PRESETS[] = {
  // 0: healthy crew member at rest (HR 60-100, SpO2 >=95, ~120/80 mmHg)
  {"Resting Normal", 68.0f, 98.0f, 42.0f, 0.015f, 1.0f, 0.0f, 0.0f, 0.0f, false,
   {33.2f, 33.5f, 33.8f, 34.0f,
    93.0f, 92.0f, 91.0f, 90.0f,
    8.0f, 9.0f, 7.0f, 8.0f, 10.0f, 9.0f,
    52000.0f, 68000.0f, 50000.0f, 66000.0f,
    45.0f}},
  // 1: sustained high +G load: tachycardia, raised pressure, bracing EMG, vibration
  {"High G-Force Load", 112.0f, 95.5f, 58.0f, 0.012f, 2.0f, 0.10f, 0.0f, 0.0f, false,
   {33.9f, 34.2f, 34.6f, 34.9f,
    112.0f, 111.0f, 110.0f, 108.0f,
    140.0f, 165.0f, 155.0f, 170.0f, 180.0f, 150.0f,
    50000.0f, 66000.0f, 48000.0f, 64000.0f,
    62.0f}},
  // 2: EVA/treadmill-class exertion near maximal HR, heavy sweating
  {"Extreme Exertion", 168.0f, 95.0f, 75.0f, 0.022f, 2.6f, 0.15f, 0.0f, 0.0f, false,
   {35.2f, 35.6f, 35.9f, 36.2f,
    118.0f, 117.0f, 116.0f, 114.0f,
    620.0f, 780.0f, 540.0f, 710.0f, 860.0f, 690.0f,
    58000.0f, 76000.0f, 56000.0f, 74000.0f,
    88.0f}},
  // 3: moderate hypoxia: SpO2 low 80s, tachycardia, peripheral cooling, weak pulse
  {"Hypoxia Simulation", 98.0f, 82.0f, 36.0f, 0.008f, 1.4f, 0.0f, 0.0f, 0.0f, false,
   {31.6f, 32.0f, 32.4f, 32.7f,
    88.0f, 87.0f, 86.0f, 85.0f,
    10.0f, 11.0f, 9.0f, 10.0f, 12.0f, 11.0f,
    46000.0f, 60000.0f, 44000.0f, 58000.0f,
    52.0f}},
  // 4: heat stress: vasodilation (lower pressure, strong PPG), hot saturated cuff
  {"Thermal Stress", 118.0f, 97.0f, 52.0f, 0.020f, 1.5f, 0.0f, 0.0f, 0.0f, false,
   {37.6f, 37.9f, 38.2f, 38.5f,
    82.0f, 81.0f, 80.0f, 79.0f,
    14.0f, 16.0f, 13.0f, 15.0f, 17.0f, 15.0f,
    60000.0f, 79000.0f, 58000.0f, 77000.0f,
    96.0f}},
  // 5: illustrative hypobaric / DCS-risk: mild SpO2 drop, local guarding EMG at e3/e4
  {"DCS Risk (Hypobaric)", 84.0f, 93.5f, 40.0f, 0.011f, 1.6f, 0.10f, 0.0f, 0.0f, false,
   {32.8f, 32.9f, 32.0f, 32.6f,
    92.0f, 91.0f, 90.0f, 89.0f,
    12.0f, 14.0f, 48.0f, 62.0f, 16.0f, 13.0f,
    50000.0f, 65000.0f, 48000.0f, 63000.0f,
    50.0f}},
  // 6: sustained load fatigue: rising RMS amplitude and physiological tremor
  {"Muscle Fatigue", 96.0f, 97.5f, 46.0f, 0.015f, 1.2f, 0.60f, 0.0f, 0.0f, false,
   {33.8f, 34.1f, 34.5f, 34.8f,
    100.0f, 99.0f, 98.0f, 97.0f,
    240.0f, 260.0f, 255.0f, 285.0f, 270.0f, 250.0f,
    51000.0f, 67000.0f, 49000.0f, 65000.0f,
    66.0f}},
  // 7: multi-system emergency + intermittent sensor glitches
  {"Emergency Malfunction", 148.0f, 74.0f, 28.0f, 0.004f, 4.0f, 0.90f, 0.03f, 1.0f, true,
   {30.8f, 31.2f, 31.9f, 32.4f,
    58.0f, 56.0f, 55.0f, 52.0f,
    85.0f, 140.0f, 40.0f, 320.0f, 95.0f, 60.0f,
    38000.0f, 50000.0f, 36000.0f, 48000.0f,
    97.0f}},
};
static const uint8_t NUM_PRESETS = sizeof(PRESETS) / sizeof(PRESETS[0]);

// ============================================================================
//  4. TELEMETRY: quantised frame + CCSDS-style packetiser
// ============================================================================
enum SensorCategory : uint8_t {   // 4-bit part of the APID
  SENSOR_CAT_FULL_FRAME   = 0x0,  // monolithic frame of all channels (used here)
  SENSOR_CAT_TEMPERATURE  = 0x1,  // reserved for per-category packets
  SENSOR_CAT_PRESSURE     = 0x2,
  SENSOR_CAT_EMG          = 0x3,
  SENSOR_CAT_PPG          = 0x4,
  SENSOR_CAT_MICROCLIMATE = 0x5,
  SENSOR_CAT_DEVICE_INFO  = 0xF,  // identity beacon: device ID + name
};

enum FrameFlags : uint8_t {
  FLAG_SIM        = 1 << 0,  // data is simulated
  FLAG_MANUAL     = 1 << 1,  // operator edited a channel
  FLAG_EMERGENCY  = 1 << 2,  // emergency scenario active
  FLAG_RANDOMIZED = 1 << 3,  // values came from "Randomize"
};

struct SensorFrame {
  uint32_t metMs;
  uint8_t  preset;
  uint8_t  flags;
  uint16_t t[4];     // 0.01 degC
  uint16_t p[4];     // 0.1 mmHg
  uint16_t e[6];     // uV RMS
  uint32_t ppg[4];   // r1, ir1, r2, ir2 raw counts
  uint16_t h;        // 0.01 %RH
};

static const size_t TM_HEADER_LEN = 6;
#if TM_APPEND_CRC16
static const size_t TM_PAYLOAD_LEN = 54;
#else
static const size_t TM_PAYLOAD_LEN = 52;
#endif
static const size_t TM_PACKET_LEN = TM_HEADER_LEN + TM_PAYLOAD_LEN;
#if TM_APPEND_CRC16
static const size_t TM_INFO_PAYLOAD_LEN = 1 + DEVICE_NAME_MAX + 2;
#else
static const size_t TM_INFO_PAYLOAD_LEN = 1 + DEVICE_NAME_MAX;
#endif
static_assert(TM_HEADER_LEN + TM_INFO_PAYLOAD_LEN <= TM_PACKET_LEN, "tx buffer too small for info packet");

static uint16_t crc16Ccitt(const uint8_t* d, size_t n) {  // poly 0x1021, init 0xFFFF
  uint16_t crc = 0xFFFF;
  while (n--) {
    crc ^= (uint16_t)(*d++) << 8;
    for (uint8_t i = 0; i < 8; i++)
      crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
  }
  return crc;
}
static inline uint8_t* put8(uint8_t* p, uint8_t v) { *p++ = v; return p; }
static inline uint8_t* put16(uint8_t* p, uint16_t v) {  // big-endian
  *p++ = (uint8_t)(v >> 8);
  *p++ = (uint8_t)(v & 0xFF);
  return p;
}
static inline uint8_t* put32(uint8_t* p, uint32_t v) {
  p = put16(p, (uint16_t)(v >> 16));
  return put16(p, (uint16_t)(v & 0xFFFF));
}

class TelemetryProtocol {
 public:
  // Builds [6-byte primary header][payload(+CRC)] into buf (>= TM_PACKET_LEN).
  size_t build(uint8_t sensorId, const SensorFrame& f, uint8_t* buf) {
    uint8_t* p = writeHeader(sensorId, TM_PAYLOAD_LEN, buf, /*track=*/true);

    // ---- Payload: fixed layout, big-endian, no padding -----------------------
    p = put32(p, f.metMs);
    p = put8(p, f.preset);
    p = put8(p, f.flags);
    for (uint8_t i = 0; i < 4; i++) p = put16(p, f.t[i]);
    for (uint8_t i = 0; i < 4; i++) p = put16(p, f.p[i]);
    for (uint8_t i = 0; i < 6; i++) p = put16(p, f.e[i]);
    for (uint8_t i = 0; i < 4; i++) p = put32(p, f.ppg[i]);
    p = put16(p, f.h);

#if TM_APPEND_CRC16
    p = put16(p, crc16Ccitt(buf, (size_t)(p - buf)));  // header + payload so far
#endif
    return (size_t)(p - buf);  // == TM_PACKET_LEN
  }

  // Identity beacon: lets the host map APID -> device ID -> device name.
  size_t buildInfo(uint8_t* buf) {
    uint8_t* p = writeHeader(SENSOR_CAT_DEVICE_INFO, TM_INFO_PAYLOAD_LEN, buf, /*track=*/false);
    p = put8(p, DEV_ID);
    char name[DEVICE_NAME_MAX] = {0};
    strncpy(name, DEV_NAME, DEVICE_NAME_MAX);
    for (uint8_t i = 0; i < DEVICE_NAME_MAX; i++) p = put8(p, (uint8_t)name[i]);
#if TM_APPEND_CRC16
    p = put16(p, crc16Ccitt(buf, (size_t)(p - buf)));
#endif
    return (size_t)(p - buf);
  }

  uint16_t lastSeq() const { return lastSeq_; }

 private:
  // CCSDS numbers bits MSB-first: bit 0 is the MSB of byte 0.
  //  word0: [15:13] version=000, [12] type=0 (TM), [11] sec.hdr flag=0,
  //         [10:0] APID = (device_id[6:0] << 4) | category[3:0]
  //  word1: [15:14] sequence flags = 11 (unsegmented), [13:0] sequence count
  //         (one counter per APID, wraps modulo 16384)
  //  word2: packet data length = (number of payload bytes) - 1
  uint8_t* writeHeader(uint8_t category, size_t payloadLen, uint8_t* buf, bool track) {
    category &= 0x0F;
    const uint16_t apid = (uint16_t)(((uint16_t)(DEV_ID & 0x7F) << 4) | category);
    const uint16_t word0 = (uint16_t)(apid & 0x07FF);  // version/type/sec-hdr bits are 0

    const uint16_t seq = seq_[category];
    seq_[category] = (uint16_t)((seq + 1) & 0x3FFF);
    if (track) lastSeq_ = seq;
    const uint16_t word1 = (uint16_t)((0x3u << 14) | seq);
    const uint16_t word2 = (uint16_t)(payloadLen - 1);

    uint8_t* p = buf;
    p = put16(p, word0);
    p = put16(p, word1);
    p = put16(p, word2);
    return p;
  }

  uint16_t seq_[16] = {0};
  uint16_t lastSeq_ = 0;
};

// ============================================================================
//  5. SENSOR MANAGER (simulation)
// ============================================================================
static float pulseShape(float ph) {  // 0..1 arterial-like pulse with small dicrotic notch
  ph -= floorf(ph);
  if (ph < 0.40f) return sinf(PI * ph / 0.40f);
  if (ph < 0.60f) return 0.15f * sinf(PI * (ph - 0.40f) / 0.20f);
  return 0.0f;
}
static const float PULSE_MEAN = 0.274f;  // average of pulseShape over one cycle

class SensorManager {
 public:
  void begin() {
    applyPreset(0);
    lastMs_ = millis();
  }

  void applyPreset(uint8_t idx) {
    if (idx >= NUM_PRESETS) idx = 0;
    presetIdx_ = idx;
    cur_ = PRESETS[idx];
    manual_ = false;
    randomized_ = false;
    for (uint8_t i = 0; i < NUM_CH; i++) { noise_[i] = 0.0f; live_[i] = cur_.base[i]; }
  }

  // Healthy values when the resting scenario is active; if a stress scenario
  // is active the values stay inside that scenario (small jitter around it).
  void randomize() {
    if (presetIdx_ == 0) {
      cur_ = PRESETS[0];
      float tw = frand(32.6f, 33.8f);                    // wrist -> elbow warms slightly
      for (uint8_t i = 0; i < 4; i++) { cur_.base[CH_T1 + i] = tw; tw += frand(0.1f, 0.4f); }
      const float map = frand(86.0f, 98.0f);             // healthy mean arterial pressure
      for (uint8_t i = 0; i < 4; i++) cur_.base[CH_P1 + i] = map - i * 1.0f + frand(-0.5f, 0.5f);
      for (uint8_t i = 0; i < 6; i++) cur_.base[CH_E1 + i] = frand(6.0f, 22.0f);  // relaxed muscle
      const float dcR = frand(42000.0f, 62000.0f), dcIR = frand(58000.0f, 80000.0f);
      cur_.base[CH_R1] = dcR;
      cur_.base[CH_IR1] = dcIR;
      cur_.base[CH_R2] = dcR * frand(0.94f, 1.0f);
      cur_.base[CH_IR2] = dcIR * frand(0.94f, 1.0f);
      cur_.base[CH_H1] = frand(38.0f, 58.0f);
      cur_.hr = frand(58.0f, 82.0f);
      cur_.spo2 = frand(96.0f, 99.0f);
      cur_.pp = frand(38.0f, 46.0f);
      cur_.perf = frand(0.012f, 0.020f);
    } else {
      cur_ = PRESETS[presetIdx_];
      for (uint8_t i = 0; i < NUM_CH; i++) {
        if (i <= CH_T4) cur_.base[i] += frand(-0.3f, 0.3f);
        else            cur_.base[i] *= 1.0f + frand(-0.06f, 0.06f);
      }
      cur_.hr *= 1.0f + frand(-0.05f, 0.05f);
      cur_.spo2 = clampf(cur_.spo2 + frand(-0.8f, 0.8f), 60.0f, 100.0f);
    }
    for (uint8_t i = 0; i < NUM_CH; i++)
      cur_.base[i] = clampf(cur_.base[i], CH_DEF[i].minV, CH_DEF[i].maxV);
    randomized_ = true;
    manual_ = false;
  }

  void setBase(uint8_t ch, float v) {
    if (ch >= NUM_CH) return;
    cur_.base[ch] = clampf(v, CH_DEF[ch].minV, CH_DEF[ch].maxV);
    manual_ = true;
    randomized_ = false;
  }

  float base(uint8_t ch) const { return cur_.base[ch]; }
  float live(uint8_t ch) const { return live_[ch]; }
  uint8_t presetIndex() const { return presetIdx_; }
  float latentHr() const { return cur_.hr; }
  float latentSpo2() const { return cur_.spo2; }
  const char* scenarioLabel() const {
    if (randomized_) return "Randomized";
    if (manual_) return "Custom";
    return cur_.name;
  }

  // Advances the simulation to nowMs and fills a quantised telemetry frame.
  void sample(uint32_t nowMs, SensorFrame& out) {
    float dt = (float)(nowMs - lastMs_) * 0.001f;
    lastMs_ = nowMs;
    if (dt <= 0.0f || dt > 0.5f) dt = TM_PERIOD_MS * 0.001f;
    const float t = (float)nowMs * 0.001f;
    const float* b = cur_.base;

    // Heart rate: respiratory sinus arrhythmia (0.25 Hz, 3%) + optional irregularity.
    hrJitter_ = 0.95f * hrJitter_ + 0.05f * gauss();
    float hr = cur_.hr * (1.0f + 0.03f * sinf(TWO_PI * 0.25f * t) + cur_.irregular * 0.18f * hrJitter_);
    hr = clampf(hr, 30.0f, 220.0f);
    const float beatHz = hr / 60.0f;
    phase_ += dt * beatHz;
    phase_ -= floorf(phase_);

    // Pulse transit time between the two PPG blocks shortens as pressure rises.
    const float mapAvg = (b[CH_P1] + b[CH_P2] + b[CH_P3] + b[CH_P4]) * 0.25f;
    const float ptt = clampf(0.028f - 0.00012f * (mapAvg - 90.0f), 0.012f, 0.040f);
    const float dPh = ptt * beatHz;  // phase delay of block 2

    // Band-limited noise (AR(1), steady-state std = sigma * noise multiplier).
    for (uint8_t i = 0; i < NUM_CH; i++) {
      float sigma;
      if (i <= CH_T4)       sigma = 0.04f;
      else if (i <= CH_P4)  sigma = 0.8f;
      else if (i <= CH_E6)  sigma = 0.10f * b[i] + 1.5f;
      else if (i <= CH_IR2) sigma = 0.004f * b[i];
      else                  sigma = 0.2f;
      noise_[i] = 0.85f * noise_[i] + 0.5268f * gauss() * sigma * cur_.noise;
    }

    for (uint8_t i = 0; i <= CH_T4; i++) live_[i] = b[i] + noise_[i];

    for (uint8_t k = 0; k < 4; k++) {
      const float ph = phase_ - k * dPh / 3.0f;  // pulse arrives later along the limb
      live_[CH_P1 + k] = b[CH_P1 + k] + cur_.pp * (pulseShape(ph) - PULSE_MEAN) + noise_[CH_P1 + k];
    }

    for (uint8_t k = 0; k < 6; k++) {
      const float mod = 1.0f + cur_.tremor * 0.35f * sinf(TWO_PI * 9.5f * t + k * 1.3f);
      live_[CH_E1 + k] = b[CH_E1 + k] * mod + noise_[CH_E1 + k];
    }

    // PPG: red/IR AC amplitude ratio R = (110 - SpO2) / 25  (empirical calibration)
    const float R = clampf((110.0f - cur_.spo2) / 25.0f, 0.3f, 1.6f);
    const float acIR = cur_.perf, acRed = cur_.perf * R;
    const float s1 = pulseShape(phase_) - PULSE_MEAN;
    const float s2 = pulseShape(phase_ - dPh) - PULSE_MEAN;
    live_[CH_R1]  = b[CH_R1]  * (1.0f + acRed * s1) + noise_[CH_R1];
    live_[CH_IR1] = b[CH_IR1] * (1.0f + acIR  * s1) + noise_[CH_IR1];
    live_[CH_R2]  = b[CH_R2]  * (1.0f + acRed * s2) + noise_[CH_R2];
    live_[CH_IR2] = b[CH_IR2] * (1.0f + acIR  * s2) + noise_[CH_IR2];

    live_[CH_H1] = b[CH_H1] + noise_[CH_H1];

    // Sensor glitch (emergency scenario only): one channel spikes for one frame.
    if (cur_.glitch > 0.0f && frand(0.0f, 1.0f) < cur_.glitch) {
      const uint8_t c = (uint8_t)(esp_random() % NUM_CH);
      live_[c] += frand(-1.0f, 1.0f) * 0.25f * (CH_DEF[c].maxV - CH_DEF[c].minV);
    }

    for (uint8_t i = 0; i < NUM_CH; i++) live_[i] = clampf(live_[i], CH_DEF[i].minV, CH_DEF[i].maxV);

    // Quantise to wire units.
    out.metMs = nowMs;
    out.preset = presetIdx_;
    out.flags = FLAG_SIM | (manual_ ? FLAG_MANUAL : 0) | (cur_.emergency ? FLAG_EMERGENCY : 0) |
                (randomized_ ? FLAG_RANDOMIZED : 0);
    for (uint8_t k = 0; k < 4; k++) out.t[k] = q16(live_[CH_T1 + k], 100.0f);
    for (uint8_t k = 0; k < 4; k++) out.p[k] = q16(live_[CH_P1 + k], 10.0f);
    for (uint8_t k = 0; k < 6; k++) out.e[k] = q16(live_[CH_E1 + k], 1.0f);
    out.ppg[0] = q18(live_[CH_R1]);
    out.ppg[1] = q18(live_[CH_IR1]);
    out.ppg[2] = q18(live_[CH_R2]);
    out.ppg[3] = q18(live_[CH_IR2]);
    out.h = q16(live_[CH_H1], 100.0f);
  }

 private:
  Preset cur_ = PRESETS[0];
  float live_[NUM_CH] = {0};
  float noise_[NUM_CH] = {0};
  float phase_ = 0.0f;
  float hrJitter_ = 0.0f;
  uint32_t lastMs_ = 0;
  uint8_t presetIdx_ = 0;
  bool manual_ = false;
  bool randomized_ = false;
};

// ============================================================================
//  6. WI-FI LINK (non-blocking, UDP)
// ============================================================================
class WifiLink {
 public:
  void begin() {
    // Unique DHCP hostname per device, e.g. "esp-007".
    snprintf(hostname_, sizeof(hostname_), "esp-%03u", (unsigned)DEV_ID);
    WiFi.persistent(false);              // do not wear flash with credentials
    WiFi.setHostname(hostname_);
    WiFi.mode(WIFI_STA);                 // station (peer) of the Linux AP
    WiFi.setSleep(false);
    WiFi.setAutoReconnect(true);
#if TM_USE_STATIC_IP
    static_assert(STATIC_HOST_BASE + 127 <= 254, "STATIC_HOST_BASE too high");
    const IPAddress ip(STATIC_NET, STATIC_HOST_BASE + DEV_ID);
    const IPAddress gw(STATIC_GATEWAY);
    const IPAddress mask(STATIC_NETMASK);
    WiFi.config(ip, gw, mask, gw);
#endif
    // Stagger retries by ID (0..~2.5 s) so devices do not hit the AP together
    // after an AP restart.
    retryMs_ = WIFI_RETRY_MS + (uint32_t)DEV_ID * 20u;
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    lastAttempt_ = millis();
  }

  void service(uint32_t now) {
    if (WiFi.status() == WL_CONNECTED) {
      if (!udpBound_) {                  // (re)open the socket after (re)connect
        udp_.begin(TM_UDP_SRC_PORT_BASE + DEV_ID);
        udpBound_ = true;
        justConnected_ = true;
      }
      return;
    }
    if (udpBound_) { udp_.stop(); udpBound_ = false; }
    if (now - lastAttempt_ >= retryMs_) {
      lastAttempt_ = now;
      WiFi.reconnect();
    }
  }

  bool connected() const { return WiFi.status() == WL_CONNECTED; }

  // True once after each (re)connection; used to announce the device right away.
  bool takeJustConnected() {
    const bool v = justConnected_;
    justConnected_ = false;
    return v;
  }

  bool send(const uint8_t* data, size_t len) {
    if (!connected() || !udpBound_) return false;
#if TM_DEST_IS_GATEWAY
    const IPAddress dest = WiFi.gatewayIP();   // the Linux host acting as AP
#else
    const IPAddress dest(TM_DEST_IP);
#endif
    if (!udp_.beginPacket(dest, TM_UDP_PORT)) return false;
    udp_.write(data, len);
    return udp_.endPacket() == 1;
  }

 private:
  WiFiUDP udp_;
  char hostname_[12] = {0};
  uint32_t lastAttempt_ = 0;
  uint32_t retryMs_ = WIFI_RETRY_MS;
  bool udpBound_ = false;
  bool justConnected_ = false;
};

// ============================================================================
//  7. DISPLAY UI
// ============================================================================
#define RGB565(r, g, b) ((uint16_t)((((r) & 0xF8) << 8) | (((g) & 0xFC) << 3) | ((b) >> 3)))
static const uint16_t COL_BG    = TFT_BLACK;
static const uint16_t COL_HDR   = RGB565(0, 170, 210);
static const uint16_t COL_SEL   = RGB565(0, 70, 110);
static const uint16_t COL_EDIT  = RGB565(150, 90, 0);
static const uint16_t COL_TEXT  = RGB565(210, 220, 230);
static const uint16_t COL_DIM   = RGB565(130, 140, 150);
static const uint16_t COL_OK    = RGB565(60, 220, 100);
static const uint16_t COL_BAD   = RGB565(255, 90, 70);
static const uint16_t COL_TOAST = RGB565(30, 120, 60);

static const uint8_t LIVE_COL0[] = {CH_T1, CH_T2, CH_T3, CH_T4, CH_P1, CH_P2, CH_P3, CH_P4, CH_H1};
static const uint8_t LIVE_COL1[] = {CH_E1, CH_E2, CH_E3, CH_E4, CH_E5, CH_E6, CH_R1, CH_IR1, CH_R2, CH_IR2};

class DisplayUI {
 public:
  enum Screen : uint8_t { SCR_MENU = 0, SCR_PRESETS, SCR_PARAMS, SCR_LIVE };

  DisplayUI(TFT_eSPI& tft, SensorManager& s, TelemetryProtocol& tm, WifiLink& w, bool& tx)
      : tft_(tft), sensors_(s), tm_(tm), wifi_(w), tx_(tx) {}

  void begin() {
    W_ = tft_.width();
    H_ = tft_.height();
    needFull_ = true;
  }

  bool editing() const { return editing_; }

  // ---- navigation ---------------------------------------------------------
  void next() {
    const uint8_t n = listCount();
    if (!n) return;
    sel_[screen_] = (uint8_t)((sel_[screen_] + 1) % n);
    needFull_ = true;
  }
  void prev() {
    const uint8_t n = listCount();
    if (!n) return;
    sel_[screen_] = (uint8_t)((sel_[screen_] + n - 1) % n);
    needFull_ = true;
  }
  void back() {
    if (editing_) { editing_ = false; }
    else if (screen_ != SCR_MENU) { screen_ = SCR_MENU; }
    else return;
    needFull_ = true;
  }
  void select() {
    switch (screen_) {
      case SCR_MENU:
        switch (sel_[SCR_MENU]) {
          case 0: screen_ = SCR_PRESETS; sel_[SCR_PRESETS] = sensors_.presetIndex(); break;
          case 1: screen_ = SCR_PARAMS; break;
          case 2: sensors_.randomize(); showToast("Values randomized", 1200); break;
          case 3: screen_ = SCR_LIVE; break;
          case 4: tx_ = !tx_; showToast(tx_ ? "Telemetry TX ON" : "Telemetry TX OFF", 1000); break;
        }
        break;
      case SCR_PRESETS:
        sensors_.applyPreset(sel_[SCR_PRESETS]);
        screen_ = SCR_MENU;
        showToast(PRESETS[sel_[SCR_PRESETS]].name, 1200);
        break;
      case SCR_PARAMS:
        editing_ = true;
        break;
      default: break;
    }
    needFull_ = true;
  }
  // dir = +1 / -1; rapid clicks (<350 ms apart) accelerate the step.
  void adjust(int dir, uint32_t now) {
    if (screen_ != SCR_PARAMS || !editing_) return;
    if (now - lastAdjMs_ < 350) { if (streak_ < 255) streak_++; } else streak_ = 0;
    lastAdjMs_ = now;
    const float mult = streak_ < 3 ? 1.0f : (streak_ < 6 ? 3.0f : (streak_ < 10 ? 10.0f : 30.0f));
    const uint8_t ch = sel_[SCR_PARAMS];
    sensors_.setBase(ch, sensors_.base(ch) + dir * CH_DEF[ch].step * mult);
    needFull_ = true;
  }

  // ---- periodic refresh (non-blocking) -----------------------------------
  void update(uint32_t now) {
    if (toastUntil_ && (int32_t)(now - toastUntil_) >= 0) { toastUntil_ = 0; needFull_ = true; }
    if (needFull_) {
      needFull_ = false;
      drawScreen();
      lastDyn_ = lastStatus_ = now;
      if (toastUntil_) drawToast();
      return;
    }
    if (toastUntil_) return;
    if (now - lastStatus_ >= 500) { lastStatus_ = now; drawStatus(); }
    if (screen_ == SCR_LIVE && now - lastDyn_ >= 250) { lastDyn_ = now; drawLiveValues(); }
  }

 private:
  static const int HDR_H = 24, LIST_Y = 28, ROW_H = 24, VISIBLE = 8;

  uint8_t listCount() const {
    switch (screen_) {
      case SCR_MENU: return 5;
      case SCR_PRESETS: return NUM_PRESETS;
      case SCR_PARAMS: return NUM_CH;
      default: return 0;
    }
  }

  void showToast(const char* msg, uint32_t ms) {
    strncpy(toast_, msg, sizeof(toast_) - 1);
    toast_[sizeof(toast_) - 1] = 0;
    toastUntil_ = millis() + ms;
    if (toastUntil_ == 0) toastUntil_ = 1;
  }

  void drawToast() {
    tft_.fillRoundRect(20, H_ / 2 - 22, W_ - 40, 44, 8, COL_TOAST);
    tft_.drawRoundRect(20, H_ / 2 - 22, W_ - 40, 44, 8, TFT_WHITE);
    tft_.setTextColor(TFT_WHITE, COL_TOAST);
    tft_.setTextDatum(MC_DATUM);
    tft_.drawString(toast_, W_ / 2, H_ / 2, 2);
  }

  void drawHeader(const char* title) {
    tft_.fillRect(0, 0, W_, HDR_H, COL_HDR);
    tft_.setTextColor(TFT_BLACK, COL_HDR);
    tft_.setTextDatum(ML_DATUM);
    tft_.drawString(title, 6, HDR_H / 2, 2);
    drawStatus();
  }

  void drawStatus() {
    char buf[32];
    snprintf(buf, sizeof(buf), "%s %s #%u", wifi_.connected() ? "WiFi" : "NoLink",
             tx_ ? "TX" : "--", (unsigned)tm_.lastSeq());
    tft_.setTextColor(TFT_BLACK, COL_HDR);
    tft_.setTextDatum(MR_DATUM);
    tft_.setTextPadding(150);
    tft_.drawString(buf, W_ - 6, HDR_H / 2, 2);
    tft_.setTextPadding(0);
  }

  void drawFooter(const char* hint, const char* idx) {
    tft_.fillRect(0, H_ - 14, W_, 14, COL_BG);
    tft_.setTextColor(COL_DIM, COL_BG);
    tft_.setTextDatum(TL_DATUM);
    tft_.drawString(hint, 4, H_ - 11, 1);
    if (idx) {
      tft_.setTextDatum(TR_DATUM);
      tft_.drawString(idx, W_ - 4, H_ - 11, 1);
    }
  }

  void drawRow(int slot, const char* left, const char* right, bool sel, bool edit) {
    const int y = LIST_Y + slot * ROW_H;
    const int h = ROW_H - 2;
    const uint16_t bg = edit ? COL_EDIT : (sel ? COL_SEL : COL_BG);
    tft_.fillRect(0, y, W_, h, bg);
    tft_.setTextColor(sel ? (uint16_t)TFT_WHITE : COL_TEXT, bg);
    tft_.setTextDatum(ML_DATUM);
    tft_.drawString(left, 10, y + h / 2, 2);
    if (right && right[0]) {
      tft_.setTextDatum(MR_DATUM);
      tft_.drawString(right, W_ - 10, y + h / 2, 2);
    }
  }

  uint8_t scrollTop(uint8_t cnt) {
    uint8_t& top = top_[screen_];
    const uint8_t sel = sel_[screen_];
    if (sel < top) top = sel;
    if (sel >= top + VISIBLE) top = (uint8_t)(sel - VISIBLE + 1);
    if (cnt <= VISIBLE) top = 0;
    return top;
  }

  void drawScreen() {
    tft_.fillScreen(COL_BG);
    char idx[12];
    switch (screen_) {
      case SCR_MENU: {
        drawHeader(DEV_NAME);
        static const char* const items[5] = {"Preset Selection", "Parameters", "Randomize Values",
                                             "Live Telemetry", "Telemetry TX"};
        for (uint8_t i = 0; i < 5; i++) {
          const char* right = "";
          if (i == 0) right = sensors_.scenarioLabel();
          if (i == 4) right = tx_ ? "ON" : "OFF";
          drawRow(i, items[i], right, i == sel_[SCR_MENU], false);
        }
        char idTxt[12];
        snprintf(idTxt, sizeof(idTxt), "ID %u", (unsigned)DEV_ID);
        drawFooter("A: next   B: select", idTxt);
        break;
      }
      case SCR_PRESETS: {
        drawHeader("Presets");
        const uint8_t top = scrollTop(NUM_PRESETS);
        for (uint8_t s = 0; s < VISIBLE && (top + s) < NUM_PRESETS; s++) {
          const uint8_t i = top + s;
          drawRow(s, PRESETS[i].name, i == sensors_.presetIndex() ? "ACTIVE" : "", i == sel_[SCR_PRESETS], false);
        }
        snprintf(idx, sizeof(idx), "%u/%u", (unsigned)(sel_[SCR_PRESETS] + 1), (unsigned)NUM_PRESETS);
        drawFooter("A: next (hold: prev)   B: apply   hold B: back", idx);
        break;
      }
      case SCR_PARAMS: {
        drawHeader(editing_ ? "Parameters - EDIT" : "Parameters");
        const uint8_t top = scrollTop(NUM_CH);
        char left[40], right[24];
        for (uint8_t s = 0; s < VISIBLE && (top + s) < NUM_CH; s++) {
          const uint8_t i = top + s;
          snprintf(left, sizeof(left), "%s  %s", CH_DEF[i].name, CH_DEF[i].desc);
          snprintf(right, sizeof(right), "%.*f %s", CH_DEF[i].decimals, sensors_.base(i), CH_DEF[i].unit);
          const bool sel = (i == sel_[SCR_PARAMS]);
          drawRow(s, left, right, sel, sel && editing_);
        }
        snprintf(idx, sizeof(idx), "%u/%u", (unsigned)(sel_[SCR_PARAMS] + 1), (unsigned)NUM_CH);
        drawFooter(editing_ ? "A: +   B: -   (fast clicks = bigger step)   hold: done"
                            : "A: next (hold: prev)   B: edit   hold B: back", idx);
        break;
      }
      case SCR_LIVE: {
        drawHeader("Live Telemetry");
        drawLiveStatic();
        drawLiveValues();
        break;
      }
    }
  }

  static const int LIVE_ROW_H = 19;

  void drawLiveStatic() {
    tft_.setTextDatum(TL_DATUM);
    tft_.setTextColor(COL_DIM, COL_BG);
    for (uint8_t i = 0; i < sizeof(LIVE_COL0); i++) {
      const int y = LIST_Y + i * LIVE_ROW_H;
      tft_.drawString(CH_DEF[LIVE_COL0[i]].name, 4, y, 2);
      tft_.drawString(CH_DEF[LIVE_COL0[i]].unit, 4 + 118, y, 2);
    }
    for (uint8_t i = 0; i < sizeof(LIVE_COL1); i++) {
      const int y = LIST_Y + i * LIVE_ROW_H;
      tft_.drawString(CH_DEF[LIVE_COL1[i]].name, 164, y, 2);
      tft_.drawString(CH_DEF[LIVE_COL1[i]].unit, 164 + 118, y, 2);
    }
  }

  void drawLiveValues() {
    char buf[24];
    tft_.setTextColor(TFT_WHITE, COL_BG);
    tft_.setTextDatum(TR_DATUM);
    tft_.setTextPadding(68);
    for (uint8_t i = 0; i < sizeof(LIVE_COL0); i++) {
      const uint8_t c = LIVE_COL0[i];
      snprintf(buf, sizeof(buf), "%.*f", CH_DEF[c].decimals, sensors_.live(c));
      tft_.drawString(buf, 4 + 114, LIST_Y + i * LIVE_ROW_H, 2);
    }
    for (uint8_t i = 0; i < sizeof(LIVE_COL1); i++) {
      const uint8_t c = LIVE_COL1[i];
      snprintf(buf, sizeof(buf), "%.*f", CH_DEF[c].decimals, sensors_.live(c));
      tft_.drawString(buf, 164 + 114, LIST_Y + i * LIVE_ROW_H, 2);
    }
    // Scenario line (HR / SpO2 are simulation latents, not transmitted channels).
    char line[48];
    snprintf(line, sizeof(line), "%s  HR %.0f  SpO2 %.1f", sensors_.scenarioLabel(),
             sensors_.latentHr(), sensors_.latentSpo2());
    tft_.setTextColor(COL_OK, COL_BG);
    tft_.setTextDatum(TL_DATUM);
    tft_.setTextPadding(W_ - 8);
    tft_.drawString(line, 4, H_ - 20, 2);
    tft_.setTextPadding(0);
  }

  TFT_eSPI& tft_;
  SensorManager& sensors_;
  TelemetryProtocol& tm_;
  WifiLink& wifi_;
  bool& tx_;

  int W_ = 320, H_ = 240;
  Screen screen_ = SCR_MENU;
  uint8_t sel_[4] = {0, 0, 0, 0};
  uint8_t top_[4] = {0, 0, 0, 0};
  bool editing_ = false;
  bool needFull_ = true;
  uint32_t lastDyn_ = 0, lastStatus_ = 0, lastAdjMs_ = 0;
  uint8_t streak_ = 0;
  char toast_[32] = {0};
  uint32_t toastUntil_ = 0;
};

// ============================================================================
//  8. GLOBALS, SETUP, LOOP
// ============================================================================
static TFT_eSPI tft;
static SensorManager sensors;
static TelemetryProtocol telemetry;
static WifiLink wifi;
static bool txEnabled = true;
static DisplayUI ui(tft, sensors, telemetry, wifi, txEnabled);

static Button btnA(BTN_A_PIN);   // EncButton: INPUT_PULLUP, active LOW by default
static Button btnB(BTN_B_PIN);

static uint8_t txBuf[TM_PACKET_LEN];
static uint32_t lastTickMs = 0;
static uint32_t lastInfoMs = 0;

void setup() {
  Serial.begin(115200);
  tft.init();
  tft.setRotation(1);  // landscape, 320x240
  tft.fillScreen(COL_BG);

  btnA.setDebTimeout(25);
  btnB.setDebTimeout(25);
  btnA.setHoldTimeout(500);
  btnB.setHoldTimeout(500);

  sensors.begin();
  wifi.begin();
  ui.begin();
  // Stagger the frame clock and the identity beacon by device ID so that many
  // devices spread their transmissions over the period instead of bursting
  // together. (The first tick/beacon fires at start + offset.)
  const uint32_t t0 = millis();
  lastTickMs = t0 - TM_PERIOD_MS + ((uint32_t)DEV_ID * TM_PERIOD_MS) / 128u;
  lastInfoMs = t0 - TM_INFO_PERIOD_MS + ((uint32_t)DEV_ID * TM_INFO_PERIOD_MS) / 128u;
  Serial.printf("Device \"%s\" (ID %u) ready, packet = %u bytes\n", DEV_NAME, (unsigned)DEV_ID,
                (unsigned)TM_PACKET_LEN);
}

void loop() {
  const uint32_t now = millis();

  // ---- input -----------------------------------------------------------------
  btnA.tick();
  btnB.tick();
  if (ui.editing()) {
    if (btnA.click()) ui.adjust(+1, now);
    if (btnB.click()) ui.adjust(-1, now);
    if (btnA.hold() || btnB.hold()) ui.back();
  } else {
    if (btnA.click()) ui.next();
    if (btnA.hold() || btnA.step()) ui.prev();
    if (btnB.click()) ui.select();
    if (btnB.hold()) ui.back();
  }

  // ---- network -----------------------------------------------------------
  wifi.service(now);

  // ---- identity beacon (on connect and periodically) ------------------------
  if (txEnabled && wifi.connected() &&
      (wifi.takeJustConnected() || now - lastInfoMs >= TM_INFO_PERIOD_MS)) {
    lastInfoMs = now;
    wifi.send(txBuf, telemetry.buildInfo(txBuf));
  }

  // ---- fixed-rate simulation + telemetry ----------------------------------
  if (now - lastTickMs >= TM_PERIOD_MS) {
    lastTickMs += TM_PERIOD_MS;
    if (now - lastTickMs >= TM_PERIOD_MS) lastTickMs = now;  // fell behind: resync

    SensorFrame frame;
    sensors.sample(now, frame);
    if (txEnabled) {
      const size_t n = telemetry.build(SENSOR_CAT_FULL_FRAME, frame, txBuf);
      wifi.send(txBuf, n);
    }
  }

  // ---- display -----------------------------------------------------------
  ui.update(now);
}
