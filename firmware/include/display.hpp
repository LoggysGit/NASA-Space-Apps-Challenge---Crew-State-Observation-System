#pragma once

#include <Arduino.h>
#include <TFT_eSPI.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "config.h"
#include "sensor_sim.hpp"

#define RGB565(r, g, b) ((uint16_t)((((r) & 0xF8) << 8) | (((g) & 0xFC) << 3) | ((b) >> 3)))
#define COLOR_BG        TFT_BLACK
#define COLOR_HEADER    RGB565(0, 160, 200)
#define COLOR_SELECTED  RGB565(0, 65, 105)
#define COLOR_TEXT      RGB565(215, 225, 235)
#define COLOR_DIM       RGB565(130, 145, 155)
#define COLOR_OK        RGB565(70, 220, 120)
#define ROW_Y0          30
#define ROW_HEIGHT      24
#define PARAM_ROWS      8
#define MENU_ITEM_COUNT 5

enum Screen : uint8_t { SCREEN_MENU, SCREEN_PRESETS, SCREEN_PARAMETERS, SCREEN_LIVE };

// Owns the TFT instance and all menu state. Button polling remains in Firmware.
class Display {
public:
  explicit Display(SensorSimulator& sensors)
    : sensors_(sensors), screen_(SCREEN_MENU), editing_(false), txEnabled_(true),
      dirty_(DIRTY_FULL), rowCount_(0), width_(320), height_(240), streak_(0),
      lastAdjustMs_(0), lastLiveMs_(0), toastUntil_(0) {
    memset(selection_, 0, sizeof(selection_));
    memset(rows_, 0, sizeof(rows_));
    memset(toast_, 0, sizeof(toast_));
    memset(liveCache_, 0, sizeof(liveCache_));
    memset(liveStatusCache_, 0, sizeof(liveStatusCache_));
  }

  void begin() {
    tft_.init();
    tft_.setRotation(1);
    tft_.fillScreen(COLOR_BG);
    width_ = tft_.width();
    height_ = tft_.height();
    dirty_ = DIRTY_FULL;
  }

  void update(uint32_t now) {
    if (toastUntil_ && (int32_t)(now - toastUntil_) >= 0) {
      toastUntil_ = 0;
      markFull();
    }
    if (dirty_ == DIRTY_FULL) {
      dirty_ = DIRTY_CLEAN;
      drawFull();
      lastLiveMs_ = now;
      return;
    }
    if (dirty_ == DIRTY_ROWS) {
      dirty_ = DIRTY_CLEAN;
      for (uint8_t i = 0; i < rowCount_; ++i) drawItem(rows_[i]);
      rowCount_ = 0;
      return;
    }
    if (screen_ == SCREEN_LIVE && !toastUntil_ && now - lastLiveMs_ >= 250) {
      lastLiveMs_ = now;
      drawLiveValues();
    }
  }

  bool txEnabled() const { return txEnabled_; }
  bool isEditing() const { return editing_; }
  void next() { move(+1); }
  void previous() { move(-1); }

  void back() {
    if (editing_) editing_ = false;
    else if (screen_ != SCREEN_MENU) screen_ = SCREEN_MENU;
    else return;
    LOGI("UI", "back -> %s%s", screenName(screen_), editing_ ? " (edit)" : "");
    markFull();
  }

  void select() {
    switch (screen_) {
      case SCREEN_MENU:
        switch (selection_[SCREEN_MENU]) {
          case 0:
            screen_ = SCREEN_PRESETS;
            selection_[SCREEN_PRESETS] = sensors_.scenarioId();
            break;
          case 1:
            screen_ = SCREEN_PARAMETERS;
            break;
          case 2:
            sensors_.randomize();
            showToast("Values randomized");
            break;
          case 3:
            screen_ = SCREEN_LIVE;
            memset(liveCache_, 0, sizeof(liveCache_));
            liveStatusCache_[0] = '\0';
            break;
          default:
            txEnabled_ = !txEnabled_;
            showToast(txEnabled_ ? "Telemetry TX ON" : "Telemetry TX OFF");
            break;
        }
        break;
      case SCREEN_PRESETS:
        sensors_.applyScenario(selection_[SCREEN_PRESETS]);
        screen_ = SCREEN_MENU;
        showToast("Scenario applied");
        break;
      case SCREEN_PARAMETERS:
        editing_ = true;
        break;
      default:
        break;
    }
    LOGI("UI", "select -> %s%s", screenName(screen_), editing_ ? " (edit)" : "");
    markFull();
  }

  void adjust(int8_t direction, uint32_t now) {
    if (screen_ != SCREEN_PARAMETERS || !editing_) return;
    if (now - lastAdjustMs_ < 350) {
      if (streak_ < 20) ++streak_;
    } else {
      streak_ = 0;
    }
    lastAdjustMs_ = now;
    const float multiplier = streak_ < 3 ? 1.0f : streak_ < 6 ? 3.0f : streak_ < 10 ? 10.0f : 30.0f;
    const uint8_t channel = selection_[SCREEN_PARAMETERS];
    const ChannelInfo& info = SensorSimulator::channelInfo(channel);
    sensors_.setBase(channel, sensors_.baseValue(channel) + direction * info.step * multiplier);
    LOGD("UI", "adjust %s -> %.2f %s (x%.0f)", info.name,
         sensors_.baseValue(channel), info.unit, multiplier);
    markRow(channel);
  }

private:
  enum DirtyState : uint8_t { DIRTY_CLEAN, DIRTY_FULL, DIRTY_ROWS };
  SensorSimulator& sensors_;
  TFT_eSPI tft_;
  uint8_t screen_;
  uint8_t selection_[4];
  bool editing_;
  bool txEnabled_;
  DirtyState dirty_;
  uint8_t rows_[4];
  uint8_t rowCount_;
  uint16_t width_;
  uint16_t height_;
  uint8_t streak_;
  uint32_t lastAdjustMs_;
  uint32_t lastLiveMs_;
  uint32_t toastUntil_;
  char toast_[32];
  char liveCache_[CHANNEL_COUNT][16];
  char liveStatusCache_[48];

  static const char* screenName(uint8_t screen) {
    switch (screen) {
      case SCREEN_MENU: return "menu";
      case SCREEN_PRESETS: return "presets";
      case SCREEN_PARAMETERS: return "parameters";
      default: return "live";
    }
  }

  uint8_t itemCount() const {
    switch (screen_) {
      case SCREEN_MENU: return MENU_ITEM_COUNT;
      case SCREEN_PRESETS: return SCENARIO_COUNT;
      case SCREEN_PARAMETERS: return CHANNEL_COUNT;
      default: return 0;
    }
  }

  uint8_t parameterTop() const {
    const uint8_t selected = selection_[SCREEN_PARAMETERS];
    return selected > PARAM_ROWS - 1 ? selected - (PARAM_ROWS - 1) : 0;
  }

  void markFull() { dirty_ = DIRTY_FULL; }

  void markRow(uint8_t item) {
    if (dirty_ == DIRTY_FULL) return;
    if (toastUntil_) { dirty_ = DIRTY_FULL; return; }
    if (dirty_ == DIRTY_CLEAN) { dirty_ = DIRTY_ROWS; rowCount_ = 0; }
    if (rowCount_ < sizeof(rows_)) rows_[rowCount_++] = item;
    else dirty_ = DIRTY_FULL;
  }

  void showToast(const char* message) {
    strncpy(toast_, message, sizeof(toast_) - 1);
    toast_[sizeof(toast_) - 1] = '\0';
    toastUntil_ = millis() + 1000;
    LOGI("UI", "toast: %s", message);
  }

  void move(int8_t direction) {
    const uint8_t count = itemCount();
    if (!count) return;
    const uint8_t oldSelection = selection_[screen_];
    const uint8_t oldTop = screen_ == SCREEN_PARAMETERS ? parameterTop() : 0;
    selection_[screen_] = (uint8_t)((oldSelection + count + direction) % count);
    const uint8_t newTop = screen_ == SCREEN_PARAMETERS ? parameterTop() : 0;
    if (oldTop != newTop) markFull();
    else { markRow(oldSelection); markRow(selection_[screen_]); }
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

  void drawRow(uint8_t row, const char* left, const char* right, bool selected) {
    const int y = ROW_Y0 + row * ROW_HEIGHT;
    const uint16_t background = selected ? COLOR_SELECTED : COLOR_BG;
    tft_.fillRect(0, y, width_, 22, background);
    tft_.setTextColor(selected ? TFT_WHITE : COLOR_TEXT, background);
    tft_.setTextDatum(ML_DATUM);
    tft_.drawString(left, 8, y + 11, 2);
    tft_.setTextDatum(MR_DATUM);
    tft_.drawString(right, width_ - 8, y + 11, 2);
  }

  void drawItem(uint8_t item) {
    char value[32];
    const bool selected = item == selection_[screen_];
    switch (screen_) {
      case SCREEN_MENU: {
        static const char* const menuItems[MENU_ITEM_COUNT] = {
          "Preset Selection", "Parameters", "Randomize Values", "Live Telemetry", "Telemetry TX"
        };
        const char* right = item == 0 ? sensors_.scenarioName() : (item == 4 ? (txEnabled_ ? "ON" : "OFF") : "");
        drawRow(item, menuItems[item], right, selected);
        break;
      }
      case SCREEN_PRESETS:
        drawRow(item, SensorSimulator::scenarioInfo(item).name,
                item == sensors_.scenarioId() ? "ACTIVE" : "", selected);
        break;
      case SCREEN_PARAMETERS: {
        const uint8_t top = parameterTop();
        if (item < top || item >= top + PARAM_ROWS) return;
        const ChannelInfo& info = SensorSimulator::channelInfo(item);
        snprintf(value, sizeof(value), "%.*f %s", info.decimals, sensors_.baseValue(item), info.unit);
        drawRow(item - top, info.name, value, selected);
        break;
      }
      default: break;
    }
  }

  void drawLiveStatic() {
    tft_.setTextColor(COLOR_DIM, COLOR_BG);
    tft_.setTextDatum(TL_DATUM);
    for (uint8_t i = 0; i < CHANNEL_COUNT; ++i) {
      const ChannelInfo& info = SensorSimulator::channelInfo(i);
      const int x = (i / 10) ? 164 : 4;
      const int y = 30 + (i % 10) * 18;
      tft_.drawString(info.name, x, y, 1);
      tft_.drawString(info.unit, x + 32, y, 1);
    }
  }

  void drawLiveValues() {
    char value[16];
    tft_.setTextColor(TFT_WHITE, COLOR_BG);
    tft_.setTextDatum(TR_DATUM);
    tft_.setTextPadding(65);
    for (uint8_t i = 0; i < CHANNEL_COUNT; ++i) {
      const ChannelInfo& info = SensorSimulator::channelInfo(i);
      snprintf(value, sizeof(value), "%.*f", info.decimals, sensors_.channelValue(i));
      if (strcmp(value, liveCache_[i]) == 0) continue;
      strcpy(liveCache_[i], value);
      const int x = (i / 10) ? 160 : 0;
      const int y = 30 + (i % 10) * 18;
      tft_.drawString(value, x + 112, y, 1);
    }
    char status[48];
    snprintf(status, sizeof(status), "%s  HR %.0f  SpO2 %.1f",
             sensors_.scenarioName(), sensors_.heartRate(), sensors_.spo2());
    if (strcmp(status, liveStatusCache_) != 0) {
      strcpy(liveStatusCache_, status);
      tft_.setTextPadding(width_ - 8);
      tft_.setTextColor(COLOR_OK, COLOR_BG);
      tft_.setTextDatum(TL_DATUM);
      tft_.drawString(status, 4, height_ - 28, 1);
    }
    tft_.setTextPadding(0);
  }

  void drawFull() {
    tft_.fillScreen(COLOR_BG);
    switch (screen_) {
      case SCREEN_MENU:
        drawHeader(DEVICE_NAME);
        for (uint8_t i = 0; i < MENU_ITEM_COUNT; ++i) drawItem(i);
        drawFooter("A: next / hold: prev   B: select / hold: back");
        break;
      case SCREEN_PRESETS:
        drawHeader("Operational Presets");
        for (uint8_t i = 0; i < SCENARIO_COUNT; ++i) drawItem(i);
        drawFooter("A: next   B: apply   hold B: back");
        break;
      case SCREEN_PARAMETERS: {
        drawHeader(editing_ ? "Parameters - EDIT" : "Raw sensor parameters");
        const uint8_t top = parameterTop();
        for (uint8_t row = 0; row < PARAM_ROWS && top + row < CHANNEL_COUNT; ++row) drawItem(top + row);
        drawFooter(editing_ ? "A: +   B: -   hold: finish" : "A: next   B: edit   hold B: back");
        break;
      }
      default:
        drawHeader("Live Sensor Data");
        drawLiveStatic();
        memset(liveCache_, 0, sizeof(liveCache_));
        liveStatusCache_[0] = '\0';
        drawLiveValues();
        break;
    }
    if (toastUntil_) {
      tft_.fillRoundRect(20, height_ / 2 - 20, width_ - 40, 40, 6, COLOR_OK);
      tft_.setTextColor(TFT_BLACK, COLOR_OK);
      tft_.setTextDatum(MC_DATUM);
      tft_.drawString(toast_, width_ / 2, height_ / 2, 2);
    }
  }
};
