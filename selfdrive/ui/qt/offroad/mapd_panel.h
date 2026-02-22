#pragma once

#include "selfdrive/ui/qt/offroad/settings.h"

class MapdPanel : public ListWidget {
  Q_OBJECT
public:
  explicit MapdPanel(SettingsWindow *parent);
  void showEvent(QShowEvent *event) override;

private slots:
  void updateState(const UIState &s);
  void updateMapdSettings();
  void checkForUpdates();
  void performUpdate();

private:
  Params params;

  // Display controls
  LabelControl *tile_status_lbl;
  LabelControl *map_storage_lbl;

  // Settings controls
  ParamControl *speed_limit_control_toggle;
  ButtonParamControl *speed_limit_offset_buttons;

  // Binary management
  ButtonControl *mapd_version_btn;
  std::string current_mapd_version;
  std::string latest_mapd_version;
  bool update_available = false;
};
