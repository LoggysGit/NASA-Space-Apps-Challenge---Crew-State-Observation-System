#pragma once

#ifndef DEVICE_ID
#define DEVICE_ID 1
#endif

#ifndef DEVICE_NAME
#define DEVICE_NAME "-"
#endif

#if DEVICE_ID < 0 || DEVICE_ID > 126
#error "DEVICE_ID must be 0..126 (APID 0x7FF is reserved)"
#endif

#define DEVICE_ID_U8     ((uint8_t)DEVICE_ID)
#define DEVICE_NAME_LEN  16

#define WIFI_SSID        "TP-Link_51C2"
#define WIFI_PASS        "21341250"
IPAddress DEST_IP(192, 168, 0, 161);

#define UDP_DEST_PORT         5005
#define UDP_SOURCE_PORT_BASE  40000

#define SAMPLE_PERIOD_MS   40      // 25 Hz
#define BATTERY_PERIOD_MS  1000    // 1 Hz
#define INFO_PERIOD_MS     2000
#define WIFI_RETRY_MS      8000

#define BTN_A_PIN  32
#define BTN_B_PIN  33

// CCSDS CUC Level 1. Update TAI_MINUS_UTC_S if the leap-second offset changes.
#define TAI_MINUS_UTC_S         37
#define UNIX_TO_CCSDS_EPOCH_S   378691200UL   // 1970-01-01 -> 1958-01-01
#define MIN_VALID_UNIX_TIME     1704067200L   // clock before this = not synced
#define TIME_CODE_SIZE          7

#define TWO_PI_F  6.2831853f

// DEBUG_LEVEL: 0 = off, 1 = errors, 2 = +warnings, 3 = +info/stats,
//              4 = +verbose (button events, hex dumps of packets)

#define DEBUG_LEVEL      3
#define DEBUG_STATS_MS   5000   // period of the status report
#define DEBUG_HEX_MS     2000   // min. gap between hex dumps of one category

#define UL(x) ((unsigned long)(x))

#if DEBUG_LEVEL >= 1
#define LOGE(tag, fmt, ...) Serial.printf("[%9lu] E %-6s " fmt "\n", UL(millis()), tag, ##__VA_ARGS__)
#else
#define LOGE(tag, fmt, ...) ((void)0)
#endif
#if DEBUG_LEVEL >= 2
#define LOGW(tag, fmt, ...) Serial.printf("[%9lu] W %-6s " fmt "\n", UL(millis()), tag, ##__VA_ARGS__)
#else
#define LOGW(tag, fmt, ...) ((void)0)
#endif
#if DEBUG_LEVEL >= 3
#define LOGI(tag, fmt, ...) Serial.printf("[%9lu] I %-6s " fmt "\n", UL(millis()), tag, ##__VA_ARGS__)
#else
#define LOGI(tag, fmt, ...) ((void)0)
#endif
#if DEBUG_LEVEL >= 4
#define LOGD(tag, fmt, ...) Serial.printf("[%9lu] D %-6s " fmt "\n", UL(millis()), tag, ##__VA_ARGS__)
#else
#define LOGD(tag, fmt, ...) ((void)0)
#endif