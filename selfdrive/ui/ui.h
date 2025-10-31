#pragma once

#include <eigen3/Eigen/Dense>
#include <memory>
#include <string>

#include <QTimer>
#include <QColor>
#include <QFuture>

#include "cereal/messaging/messaging.h"
#include "common/mat.h"
#include "common/params.h"
#include "common/util.h"
#include "system/hardware/hw.h"
#include "selfdrive/ui/qt/prime_state.h"

const int UI_BORDER_SIZE = 30;
const int UI_HEADER_HEIGHT = 420;

const int UI_FREQ = 20; // Hz
const int BACKLIGHT_OFFROAD = 50;

const Eigen::Matrix3f VIEW_FROM_DEVICE = (Eigen::Matrix3f() <<
  0.0, 1.0, 0.0,
  0.0, 0.0, 1.0,
  1.0, 0.0, 0.0).finished();

const Eigen::Matrix3f FCAM_INTRINSIC_MATRIX = (Eigen::Matrix3f() <<
  2648.0, 0.0, 1928.0 / 2,
  0.0, 2648.0, 1208.0 / 2,
  0.0, 0.0, 1.0).finished();

// tici ecam focal probably wrong? magnification is not consistent across frame
// Need to retrain model before this can be changed
const Eigen::Matrix3f ECAM_INTRINSIC_MATRIX = (Eigen::Matrix3f() <<
  567.0, 0.0, 1928.0 / 2,
  0.0, 567.0, 1208.0 / 2,
  0.0, 0.0, 1.0).finished();

typedef enum UIStatus {
  STATUS_DISENGAGED,
  STATUS_OVERRIDE,
  STATUS_ENGAGED,
} UIStatus;

const QColor bg_colors [] = {
  [STATUS_DISENGAGED] = QColor(0x17, 0x33, 0x49, 0xc8),
  [STATUS_OVERRIDE] = QColor(0x91, 0x9b, 0x95, 0xf1),
  [STATUS_ENGAGED] = QColor(0x17, 0x86, 0x44, 0xf1),
};

typedef struct UIScene {
  Eigen::Matrix3f view_from_calib = VIEW_FROM_DEVICE;
  Eigen::Matrix3f view_from_wide_calib = VIEW_FROM_DEVICE;
  cereal::PandaState::PandaType pandaType;

  cereal::LongitudinalPersonality personality;

  float light_sensor = -1;
  bool started, ignition, is_metric, recording_audio;
  uint64_t started_frame;

  // BMW diagnostic data
  bool bmw_diagnostics_available = false;
  float bmw_coolant_temp = 0.0;
  float bmw_oil_temp = 0.0;
  int bmw_dtc_count = 0;
  char bmw_car_fingerprint[32] = "";

  // Extended BMW diagnostics
  float bmw_intake_air_temp = 0.0;
  float bmw_exhaust_gas_temp = 0.0;
  float bmw_fuel_rail_pressure = 0.0;
  float bmw_turbo_boost_pressure = 0.0;
  int bmw_engine_rpm = 0;
  float bmw_engine_load = 0.0;
  float bmw_battery_voltage = 0.0;
  int bmw_protection_mode = 0;  // 0=Normal, 1=Reduce, 2=Limp
  float bmw_thermal_stress = 0.0;
  bool bmw_dtc_active = false;
  char bmw_active_dtcs[256] = "";  // Comma-separated DTC codes
  char bmw_dtc_clear_status[128] = "";  // DTC clear eligibility status

  // Personalized longitudinal learning data
  float personalized_scales[5] = {1.0, 1.0, 1.0, 1.0, 1.0};  // Scale factors for VREL_BP intervals
  uint16_t personalized_valid_blocks[5] = {0, 0, 0, 0, 0};   // Valid blocks per interval
  uint8_t personalized_progress = 0;                          // Learning progress 0-100%
  uint8_t personalized_status = 0;                            // 0=unlearned, 1=learning, 2=learned, 3=invalid
  int8_t personalized_active_interval = -1;                   // Currently learning interval (-1 if none)

  // Longitudinal actuator delay learning data
  float longitudinal_delay = 0.0;                             // Current longitudinal delay (seconds)
  float longitudinal_delay_estimate = 0.0;                    // Estimated delay
  float longitudinal_delay_std = 0.0;                         // Standard deviation
  int longitudinal_valid_blocks = 0;                          // Valid blocks count
  uint8_t longitudinal_status = 0;                            // 0=unestimated, 1=estimated, 2=invalid
  int8_t longitudinal_cal_perc = 0;                           // Calibration percentage

  // Lateral actuator delay learning data
  float lateral_delay = 0.0;                                  // Current lateral delay (seconds)
  float lateral_delay_estimate = 0.0;                         // Estimated delay
  float lateral_delay_std = 0.0;                              // Standard deviation
  int lateral_valid_blocks = 0;                               // Valid blocks count
  uint8_t lateral_status = 0;                                 // 0=unestimated, 1=estimated, 2=invalid
  int8_t lateral_cal_perc = 0;                                // Calibration percentage
} UIScene;

class UIState : public QObject {
  Q_OBJECT

public:
  UIState(QObject* parent = 0);
  void updateStatus();
  inline bool engaged() const {
    return scene.started && (*sm)["selfdriveState"].getSelfdriveState().getEnabled();
  }

  std::unique_ptr<SubMaster> sm;
  UIStatus status;
  UIScene scene = {};
  QString language;
  PrimeState *prime_state;

signals:
  void uiUpdate(const UIState &s);
  void offroadTransition(bool offroad);
  void engagedChanged(bool engaged);

private slots:
  void update();

private:
  QTimer *timer;
  bool started_prev = false;
  bool engaged_prev = false;
};

UIState *uiState();

// device management class
class Device : public QObject {
  Q_OBJECT

public:
  Device(QObject *parent = 0);
  bool isAwake() { return awake; }
  void setOffroadBrightness(int brightness) {
    offroad_brightness = std::clamp(brightness, 0, 100);
  }

private:
  bool awake = false;
  int interactive_timeout = 0;
  bool ignition_on = false;

  int offroad_brightness = BACKLIGHT_OFFROAD;
  int last_brightness = 0;
  FirstOrderFilter brightness_filter;
  QFuture<void> brightness_future;

  void updateBrightness(const UIState &s);
  void updateWakefulness(const UIState &s);
  void setAwake(bool on);

signals:
  void displayPowerChanged(bool on);
  void interactiveTimeout();

public slots:
  void resetInteractiveTimeout(int timeout = -1);
  void update(const UIState &s);
};

Device *device();
void ui_update_params(UIState *s);
