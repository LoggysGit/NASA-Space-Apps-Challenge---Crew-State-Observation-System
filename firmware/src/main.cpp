#include <Arduino.h>
#include <WiFi.h>
#include <EncButton.h>
#include <esp_system.h>

#include "config.h"
#include "system.hpp"
#include "sensor_sim.hpp"
#include "transport_protocol.hpp"
#include "wifi_transport.hpp"
#include "display.hpp"

// Firmware is the composition root: it owns the subsystems and coordinates
// their lifecycle. Signal-generation and packet-format logic live in their
// respective components.
class Firmware {
 public:
  Firmware() : protocol_(sensors_), display_(sensors_) {}

  void begin() {
    Serial.begin(115200);

    system_.seedRandom(esp_random() | 1UL);
    sensors_.seedRandom(esp_random() | 1UL);

    LOGI("BOOT", "ASTRO-ARM simulator: device %u \"%s\"", DEVICE_ID_U8, DEVICE_NAME);
    LOGI("BOOT", "chip %s @ %u MHz, reset reason %d, free heap %lu",
         ESP.getChipModel(), (unsigned)ESP.getCpuFreqMHz(),
         (int)esp_reset_reason(), UL(ESP.getFreeHeap()));
    LOGI("BOOT", "dest %s:%u, sample %u ms, debug level %d",
         DEST_IP.toString().c_str(), UDP_DEST_PORT, SAMPLE_PERIOD_MS, DEBUG_LEVEL);

    buttonA_.setDebTimeout(25);
    buttonB_.setDebTimeout(25);
    buttonA_.setHoldTimeout(500);
    buttonB_.setHoldTimeout(500);

    display_.begin();
    sensors_.begin();
    wifi_.begin(&system_.stats());

    const uint32_t now = millis();
    lastInfoMs_ = now - INFO_PERIOD_MS;  // INFO immediately after connection
    lastBatteryPacketMs_ = now;
    system_.stats().lastReportMs = now;
    sensors_.setLastSampleTime(now - SAMPLE_PERIOD_MS);  // first sample immediately
  }

  void update() {
    const uint32_t loopStartUs = micros();
    const uint32_t now = millis();

    processButtons(now);
    wifi_.service(now);
    system_.serviceTime(now, wifi_.connected());

    // INFO packet: on every (re)connect and periodically afterwards.
    const bool connectEvent = wifi_.consumeConnectionEvent();
    if (txReady() && (connectEvent || now - lastInfoMs_ >= INFO_PERIOD_MS)) {
      lastInfoMs_ = now;
      uint8_t timestamp[TIME_CODE_SIZE];
      if (system_.writeTimeCode(timestamp)) {
        txSend(CAT_DEVICE_INFO, timestamp);
      } else {
        system_.stats().noTimeDrops++;
      }
    }

    // Sensor packets at the configured sample rate.
    if (sensors_.sampleDue(now, SAMPLE_PERIOD_MS)) {
      sensors_.sample(now);
      if (txReady()) {
        static const uint8_t categories[] = {
          CAT_TMP117, CAT_BMP390, CAT_ADS1298, CAT_MAX86141, CAT_SHT45
        };
        txSendWithTime(categories, sizeof(categories));
      }
    }

    // Battery packet at 1 Hz.
    if (txReady() && now - lastBatteryPacketMs_ >= BATTERY_PERIOD_MS) {
      lastBatteryPacketMs_ = now;
      static const uint8_t categories[] = {CAT_BQ27441};
      txSendWithTime(categories, sizeof(categories));
    }

    display_.update(now);
    debugReport(now);

    const uint32_t loopUs = micros() - loopStartUs;
    if (loopUs > system_.stats().loopMaxUs) {
      system_.stats().loopMaxUs = loopUs;
    }
  }

 private:
  System system_;
  SensorSimulator sensors_;
  TransportProtocol protocol_;
  WiFiTransport wifi_;
  Display display_;

  Button buttonA_{BTN_A_PIN};
  Button buttonB_{BTN_B_PIN};

  uint8_t txBuffer_[MAX_PACKET_SIZE] = {};
  uint32_t lastInfoMs_ = 0;
  uint32_t lastBatteryPacketMs_ = 0;
  uint32_t lastHexMs_[16] = {};
  uint32_t lastTxFailureLogMs_ = 0;
  uint32_t lastNoTimeLogMs_ = 0;

  bool txReady() const {
    return display_.txEnabled() && wifi_.connected();
  }

  bool rateLimit(uint32_t& last, uint32_t now, uint32_t periodMs) {
    if (last != 0 && now - last < periodMs) return false;
    last = now;
    return true;
  }

  void txNoteFailure(SendResult result) {
    Stats& stats = system_.stats();
    stats.sendFail++;
    if (rateLimit(lastTxFailureLogMs_, millis(), 2000)) {
      LOGW("TX", "send failed: %s (total failures %lu)",
           SEND_RESULT_NAMES[result], UL(stats.sendFail));
    }
  }

#if DEBUG_LEVEL >= 4
  void txHexDump(uint8_t category, const uint8_t* data, size_t length) {
    if (!rateLimit(lastHexMs_[category & 0x0F], millis(), DEBUG_HEX_MS)) return;
    char line[MAX_PACKET_SIZE * 3 + 1];
    char* out = line;
    for (size_t i = 0; i < length; ++i) out += sprintf(out, "%02X ", data[i]);
    LOGD("HEX", "%-8s len=%u: %s",
         protocol_.categoryName(category), (unsigned)length, line);
  }
#endif

  void txSend(uint8_t category, const uint8_t* timestamp) {
    const size_t length = category == CAT_DEVICE_INFO
        ? protocol_.buildInfo(timestamp, txBuffer_, sizeof(txBuffer_))
        : protocol_.buildSensor(category, timestamp, txBuffer_, sizeof(txBuffer_));

    if (!length) {
      system_.stats().buildFail++;
      return;
    }

    const SendResult result = wifi_.send(txBuffer_, length);
    if (result == SEND_OK) {
      system_.stats().sent[category & 0x0F]++;
#if DEBUG_LEVEL >= 4
      txHexDump(category, txBuffer_, length);
#endif
    } else {
      txNoteFailure(result);
    }
  }

  void txSendWithTime(const uint8_t* categories, uint8_t count) {
    uint8_t timestamp[TIME_CODE_SIZE];
    if (!system_.writeTimeCode(timestamp)) {
      Stats& stats = system_.stats();
      stats.noTimeDrops += count;
      if (rateLimit(lastNoTimeLogMs_, millis(), 5000)) {
        LOGW("TX", "clock not synchronized - dropped %lu packets so far",
             UL(stats.noTimeDrops));
      }
      return;
    }
    for (uint8_t i = 0; i < count; ++i) txSend(categories[i], timestamp);
  }

  void processButtons(uint32_t now) {
    buttonA_.tick();
    buttonB_.tick();

    if (display_.isEditing()) {
      if (buttonA_.click()) {
        LOGD("BTN", "A click (edit +)");
        display_.adjust(+1, now);
      }
      if (buttonB_.click()) {
        LOGD("BTN", "B click (edit -)");
        display_.adjust(-1, now);
      }
      if (buttonA_.hold() || buttonB_.hold()) {
        LOGD("BTN", "hold (finish edit)");
        display_.back();
      }
      return;
    }

    if (buttonA_.click()) {
      LOGD("BTN", "A click");
      display_.next();
    }
    if (buttonA_.hold() || buttonA_.step()) {
      LOGD("BTN", "A hold/step");
      display_.previous();
    }
    if (buttonB_.click()) {
      LOGD("BTN", "B click");
      display_.select();
    }
    if (buttonB_.hold()) {
      LOGD("BTN", "B hold");
      display_.back();
    }
  }

  void debugReport(uint32_t now) {
#if DEBUG_LEVEL >= 3
    Stats& stats = system_.stats();
    if (now - stats.lastReportMs < DEBUG_STATS_MS) return;

    uint32_t total = 0;
    for (uint8_t i = 0; i < 16; ++i) total += stats.sent[i];
    const uint32_t elapsed = now - stats.lastReportMs;
    const uint32_t packetsPerSecond = elapsed
        ? (uint32_t)(((uint64_t)(total - stats.lastTotalSent) * 1000ULL) / elapsed)
        : 0;

    const wl_status_t wifiStatus = WiFi.status();
    LOGI("STAT", "up=%lus heap=%lu (min %lu) loopMax=%luus",
         UL(now / 1000), UL(ESP.getFreeHeap()), UL(ESP.getMinFreeHeap()), UL(stats.loopMaxUs));
    LOGI("STAT", "wifi=%s rssi=%d ip=%s time=%s tx=%s",
         wifi_.statusName(wifiStatus), wifiStatus == WL_CONNECTED ? WiFi.RSSI() : 0,
         WiFi.localIP().toString().c_str(), system_.timeSynced() ? "synced" : "NOT synced",
         display_.txEnabled() ? "on" : "off");
    LOGI("STAT", "sent %lu pkt/s | TMP=%lu BMP=%lu ADS=%lu MAX=%lu SHT=%lu BQ=%lu INFO=%lu",
         UL(packetsPerSecond), UL(stats.sent[CAT_TMP117]), UL(stats.sent[CAT_BMP390]),
         UL(stats.sent[CAT_ADS1298]), UL(stats.sent[CAT_MAX86141]),
         UL(stats.sent[CAT_SHT45]), UL(stats.sent[CAT_BQ27441]),
         UL(stats.sent[CAT_DEVICE_INFO]));
    LOGI("STAT", "fail: send=%lu build=%lu noTime=%lu | wifi up=%lu down=%lu",
         UL(stats.sendFail), UL(stats.buildFail), UL(stats.noTimeDrops),
         UL(stats.wifiConnects), UL(stats.wifiDisconnects));
    LOGI("STAT", "scenario=%s HR=%.0f SpO2=%.1f flags=0x%02X",
         sensors_.scenarioName(), sensors_.heartRate(), sensors_.spo2(), sensors_.flags());

    stats.lastTotalSent = total;
    stats.lastReportMs = now;
    stats.loopMaxUs = 0;
#else
    (void)now;
#endif
  }
};

// Arduino framework entry points. All application state is owned by Firmware.
Firmware firmware;

void setup() {
  firmware.begin();
}

void loop() {
  firmware.update();
}
