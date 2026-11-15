#pragma once

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <stdint.h>
#include <stddef.h>

#include "config.h"
#include "system.hpp"

// =============================================================================
// Wi-Fi / UDP transport
// Owns the station connection lifecycle and the UDP socket.
// =============================================================================

enum SendResult : uint8_t {
  SEND_OK = 0,
  SEND_NOT_CONNECTED,
  SEND_BEGIN_FAIL,
  SEND_SHORT_WRITE,
  SEND_END_FAIL
};

static const char* const SEND_RESULT_NAMES[] = {
  "ok",
  "not connected",
  "beginPacket",
  "short write",
  "endPacket"
};

class WiFiTransport {
public:
  WiFiTransport()
    : udpStarted_(false),
      justConnected_(false),
      lastWifiAttemptMs_(0),
      stats_(nullptr) {
  }

  // Optional Stats pointer allows Wi-Fi event counters to be included in the
  // central firmware report without introducing a global Stats instance.
  void begin(Stats* stats = nullptr) {
    stats_ = stats;

    char hostname[20];
    snprintf(
      hostname,
      sizeof(hostname),
      "astro-%03u",
      DEVICE_ID_U8
    );

    // The callback is bound to this instance. It only updates counters and
    // emits short log messages because it runs in the Wi-Fi event task.
    WiFi.onEvent(
      [this](arduino_event_id_t event, arduino_event_info_t info) {
        onWiFiEvent(event, info);
      }
    );

    WiFi.persistent(false);
    WiFi.setHostname(hostname);
    WiFi.mode(WIFI_STA);
    WiFi.setSleep(false);
    WiFi.setAutoReconnect(true);
    WiFi.begin(WIFI_SSID, WIFI_PASS);

    // NTP provides UTC; System::TimeCode converts it to the CCSDS TAI epoch.
    configTime(0, 0, "pool.ntp.org", "time.nist.gov");

    lastWifiAttemptMs_ = millis();

    LOGI("WIFI", "connecting to \"%s\" as %s", WIFI_SSID, hostname);
  }

  void service(uint32_t now) {
    if (WiFi.status() == WL_CONNECTED) {
      if (!udpStarted_) {
        const uint16_t localPort =
          static_cast<uint16_t>(UDP_SOURCE_PORT_BASE + DEVICE_ID_U8);

        udpStarted_ = (udp_.begin(localPort) != 0);

        if (udpStarted_) {
          justConnected_ = true;
          LOGI(
            "UDP",
            "socket open on local port %u, destination %s:%u",
            localPort,
            DEST_IP.toString().c_str(),
            UDP_DEST_PORT
          );
        } else {
          LOGE("UDP", "udp.begin(%u) failed", localPort);
        }
      }

      return;
    }

    if (udpStarted_) {
      udp_.stop();
      udpStarted_ = false;
      LOGW("UDP", "socket closed (Wi-Fi down)");
    }

    const uint32_t retryPeriod =
      WIFI_RETRY_MS + static_cast<uint32_t>(DEVICE_ID_U8) * 20U;

    if (now - lastWifiAttemptMs_ >= retryPeriod) {
      lastWifiAttemptMs_ = now;
      LOGW("WIFI", "retrying, status=%s", statusName(WiFi.status()));
      WiFi.reconnect();
    }
  }

  bool connected() const {
    return udpStarted_ && WiFi.status() == WL_CONNECTED;
  }

  bool consumeConnectionEvent() {
    const bool result = justConnected_;
    justConnected_ = false;
    return result;
  }

  SendResult send(const uint8_t* data, size_t length) {
    if (!connected() || !data || length == 0) {
      return SEND_NOT_CONNECTED;
    }

    if (!udp_.beginPacket(DEST_IP, UDP_DEST_PORT)) {
      return SEND_BEGIN_FAIL;
    }

    if (udp_.write(data, length) != length) {
      return SEND_SHORT_WRITE;
    }

    if (udp_.endPacket() != 1) {
      return SEND_END_FAIL;
    }

    return SEND_OK;
  }

  const char* statusName(wl_status_t status) const {
    switch (status) {
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

private:
  WiFiUDP udp_;
  bool udpStarted_;
  bool justConnected_;
  uint32_t lastWifiAttemptMs_;
  Stats* stats_;

  void onWiFiEvent(
    arduino_event_id_t event,
    arduino_event_info_t info
  ) {
    switch (event) {
      case ARDUINO_EVENT_WIFI_STA_START:
        LOGI("WIFI", "station started");
        break;

      case ARDUINO_EVENT_WIFI_STA_CONNECTED:
        LOGI(
          "WIFI",
          "associated with AP, channel %u",
          static_cast<unsigned>(info.wifi_sta_connected.channel)
        );
        break;

      case ARDUINO_EVENT_WIFI_STA_GOT_IP:
        if (stats_) {
          ++stats_->wifiConnects;
        }
        LOGI(
          "WIFI",
          "got IP %s",
          IPAddress(info.got_ip.ip_info.ip.addr).toString().c_str()
        );
        break;

      case ARDUINO_EVENT_WIFI_STA_DISCONNECTED:
        if (stats_) {
          ++stats_->wifiDisconnects;
        }
        LOGW(
          "WIFI",
          "disconnected, reason %u",
          static_cast<unsigned>(info.wifi_sta_disconnected.reason)
        );
        break;

      default:
        break;
    }
  }
};
