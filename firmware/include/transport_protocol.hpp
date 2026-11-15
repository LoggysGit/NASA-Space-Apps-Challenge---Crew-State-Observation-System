#pragma once

#include <Arduino.h>
#include <stdint.h>
#include <math.h>
#include <stddef.h>
#include <string.h>

#include "config.h"
#include "sensor_sim.hpp"

// =============================================================================
// ASTRO-ARM telemetry packet protocol
//
// CCSDS primary header (6 bytes) + application payload + CRC-16.
// One sequence counter is maintained per packet category.
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

class TransportProtocol {
public:
  explicit TransportProtocol(const SensorSimulator& sensors)
    : sensors_(sensors),
      lastSequence_(0) {
    memset(sequence_, 0, sizeof(sequence_));
  }

  const char* categoryName(uint8_t category) const {
    switch (category) {
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

  // Builds the CCSDS device information packet.
  // Payload: timestamp[7], device ID[1], device name[16].
  size_t buildInfo(
    const uint8_t* timestamp,
    uint8_t* output,
    size_t capacity
  ) {
    if (!timestamp || !output) {
      LOGE("PROTO", "INFO: null input/output buffer");
      return 0;
    }

    uint8_t payload[MAX_PAYLOAD_SIZE] = {};
    uint8_t* p = payload;

    memcpy(p, timestamp, TIME_CODE_SIZE);
    p += TIME_CODE_SIZE;

    *p++ = DEVICE_ID_U8;
    strncpy(reinterpret_cast<char*>(p), DEVICE_NAME, DEVICE_NAME_LEN);
    p += DEVICE_NAME_LEN;

    return buildPacket(
      CAT_DEVICE_INFO,
      payload,
      static_cast<size_t>(p - payload),
      output,
      capacity
    );
  }

  // Builds one sensor packet. Timestamp is shared by all packets belonging
  // to the same sampling cycle.
  //
  // SensorSimulator::channelValue() uses this channel order:
  // T1..T4=0..3, P1..P4=4..7, E1..E6=8..13,
  // RED1=14, IR1=15, RED2=16, IR2=17, H1=18, VBAT=19.
  size_t buildSensor(
    uint8_t category,
    const uint8_t* timestamp,
    uint8_t* output,
    size_t capacity
  ) {
    if (!timestamp || !output) {
      LOGE("PROTO", "%s: null input/output buffer", categoryName(category));
      return 0;
    }

    uint8_t payload[MAX_PAYLOAD_SIZE] = {};
    uint8_t* p = payload;

    memcpy(p, timestamp, TIME_CODE_SIZE);
    p += TIME_CODE_SIZE;

    p = putU8(p, sensors_.scenarioId());
    p = putU8(p, sensors_.flags());

    switch (category) {
      case CAT_TMP117:
        for (uint8_t i = 0; i < 4; ++i) {
          p = putU16(p, toU16(sensors_.channelValue(i), 100.0f));
        }
        break;

      case CAT_BMP390:
        for (uint8_t i = 0; i < 4; ++i) {
          p = putU32(p, toU32(sensors_.channelValue(4 + i), 100.0f));
        }
        break;

      case CAT_ADS1298:
        for (uint8_t i = 0; i < 6; ++i) {
          p = putU16(p, toU16(sensors_.channelValue(8 + i), 1.0f));
        }
        break;

      case CAT_MAX86141:
        for (uint8_t i = 0; i < 4; ++i) {
          p = putU32(p, toU32(sensors_.channelValue(14 + i), 1.0f));
        }
        break;

      case CAT_SHT45:
        p = putU16(p, toU16(sensors_.channelValue(18), 100.0f));
        break;

      case CAT_BQ27441:
        p = putU16(p, toU16(sensors_.channelValue(19), 1.0f));
        break;

      default:
        LOGE("PROTO", "unknown category 0x%X", category);
        return 0;
    }

    return buildPacket(
      category,
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
  const SensorSimulator& sensors_;
  uint16_t sequence_[16];
  uint16_t lastSequence_;

  uint8_t* putU8(uint8_t* p, uint8_t value) const {
    *p++ = value;
    return p;
  }

  uint8_t* putU16(uint8_t* p, uint16_t value) const {
    *p++ = static_cast<uint8_t>(value >> 8);
    *p++ = static_cast<uint8_t>(value);
    return p;
  }

  uint8_t* putU32(uint8_t* p, uint32_t value) const {
    p = putU16(p, static_cast<uint16_t>(value >> 16));
    return putU16(p, static_cast<uint16_t>(value));
  }

  uint16_t toU16(float value, float scale) const {
    long q = lroundf(value * scale);
    if (q < 0) q = 0;
    if (q > 65535) q = 65535;
    return static_cast<uint16_t>(q);
  }

  uint32_t toU32(float value, float scale) const {
    double q = round(static_cast<double>(value) * scale);
    if (q < 0.0) q = 0.0;
    if (q > 4294967295.0) q = 4294967295.0;
    return static_cast<uint32_t>(q);
  }

  uint16_t crc16(const uint8_t* data, size_t length) const {
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
      LOGE("PROTO", "%s: bad payload length %u",
           categoryName(category), static_cast<unsigned>(payloadLength));
      return 0;
    }

    const size_t totalLength =
      CCSDS_HEADER_SIZE + payloadLength + CCSDS_CRC_SIZE;

    if (capacity < totalLength) {
      LOGE("PROTO", "%s: buffer too small (%u < %u)",
           categoryName(category),
           static_cast<unsigned>(capacity),
           static_cast<unsigned>(totalLength));
      return 0;
    }

    const uint8_t index = category & 0x0F;
    const uint16_t apid = static_cast<uint16_t>(
      ((DEVICE_ID_U8 & 0x7F) << 4) | index
    );

    const uint16_t sequence = sequence_[index];
    sequence_[index] = static_cast<uint16_t>((sequence + 1) & 0x3FFF);
    lastSequence_ = sequence;

    uint8_t* p = output;
    p = putU16(p, apid);
    p = putU16(p, static_cast<uint16_t>(0xC000 | sequence));

    // CCSDS Packet Data Length counts the complete data field, including CRC,
    // minus one. This matches the original ASTRO-ARM packet format.
    p = putU16(
      p,
      static_cast<uint16_t>(payloadLength + CCSDS_CRC_SIZE - 1)
    );

    memcpy(p, payload, payloadLength);
    p += payloadLength;

    const uint16_t checksum = crc16(
      output,
      CCSDS_HEADER_SIZE + payloadLength
    );
    p = putU16(p, checksum);

    return static_cast<size_t>(p - output);
  }
};
