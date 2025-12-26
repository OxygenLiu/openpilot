#pragma once

#include "selfdrive/ui/qt/offroad/settings.h"

class DeveloperPanel : public ListWidget {
  Q_OBJECT
public:
  explicit DeveloperPanel(SettingsWindow *parent);
  void showEvent(QShowEvent *event) override;

private slots:
  void updateToggles(bool _offroad);
  void openLateralDelayDetails();
  void updateState(const UIState &s);

private:
  Params params;
  ParamControl* adbToggle;
  ParamControl* joystickToggle;
  ParamControl* longManeuverToggle;
  ParamControl* dccCalibrationToggle;
  ParamControl* experimentalLongitudinalToggle;
  LabelControl *lateral_delay_lbl;
  ButtonControl *lateral_delay_details_btn;
  bool is_release;
  bool offroad = false;
};
