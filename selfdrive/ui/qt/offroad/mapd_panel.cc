#include "selfdrive/ui/qt/offroad/mapd_panel.h"
#include "selfdrive/ui/qt/widgets/controls.h"
#include <QProcess>
#include <QJsonDocument>
#include <QJsonObject>

MapdPanel::MapdPanel(SettingsWindow *parent) : ListWidget(parent) {
  // === Mapd Settings Section ===

  // Speed Limit Control toggle (master control)
  speed_limit_control_toggle = new ParamControl(
    "MapdSpeedLimitControlEnabled",
    tr("Map Speed Limit and Control"),
    tr("Enable automatic speed limit control based on map data. When enabled, openpilot will respect speed limits and slow down for curves using both map geometry and vision detection."),
    ""
  );
  addItem(speed_limit_control_toggle);
  QObject::connect(speed_limit_control_toggle, &ParamControl::toggleFlipped, this, &MapdPanel::updateMapdSettings);

  // Set default offset to +10% (index 2) if not set
  std::string offset_value = params.get("MapdSpeedLimitOffsetPercent");
  if (offset_value.empty()) {
    params.put("MapdSpeedLimitOffsetPercent", "2");  // Default to +10%
  }

  // Speed Limit Offset buttons (percentage-based with +15% warning)
  speed_limit_offset_buttons = new ButtonParamControl(
    "MapdSpeedLimitOffsetPercent",
    tr("Speed Limit Offset"),
    tr("Increase the speed limit by a percentage. Select 0%, +5%, +10%, or +15% offset. Warning: +15% may increase ticket risk."),
    "",
    {tr("0%"), tr("+5%"), tr("+10%"), tr("+15%")}
  );
  addItem(speed_limit_offset_buttons);
  QObject::connect(speed_limit_offset_buttons, &ButtonParamControl::buttonClicked, this, &MapdPanel::updateMapdSettings);

  // === Binary Management Section ===

  // Mapd version display with check for updates button
  mapd_version_btn = new ButtonControl(tr("Mapd Version"), tr("CHECK"));
  addItem(mapd_version_btn);
  QObject::connect(mapd_version_btn, &ButtonControl::clicked, this, &MapdPanel::checkForUpdates);

  // Read current version from file (or default to v2.0.2)
  std::string current_version_str = params.get("MapdVersion");
  if (current_version_str.empty()) {
    current_mapd_version = "v2.0.2";
    params.put("MapdVersion", current_mapd_version);  // Save default version
  } else {
    current_mapd_version = current_version_str;
  }
  mapd_version_btn->setValue(QString::fromStdString(current_mapd_version));

  // === Status Display Section ===

  // Tile status display
  tile_status_lbl = new LabelControl(tr("Map Tile Status"), tr("Checking..."));
  addItem(tile_status_lbl);

  // Map storage display
  map_storage_lbl = new LabelControl(tr("Offline Map Storage"), tr("Calculating..."));
  addItem(map_storage_lbl);

  // Connect to UI state updates
  QObject::connect(uiState(), &UIState::uiUpdate, this, &MapdPanel::updateState);
}

void MapdPanel::showEvent(QShowEvent *event) {
  ListWidget::showEvent(event);

  // Update map storage info when panel is shown
  QProcess *du = new QProcess(this);
  du->start("du", QStringList() << "-sh" << "/data/media/0/osm/offline");
  QObject::connect(du, QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished),
    [=](int exitCode, QProcess::ExitStatus exitStatus) {
      if (exitCode == 0) {
        QString output = du->readAllStandardOutput().trimmed();
        QStringList parts = output.split('\t');
        if (parts.size() >= 1) {
          map_storage_lbl->setText(parts[0]);
        }
      } else {
        map_storage_lbl->setText(tr("Error reading storage"));
      }
      du->deleteLater();
    });

  // Automatically check for mapd updates when panel is shown (if not already in update state)
  if (!update_available) {
    checkForUpdates();
  }
}

void MapdPanel::updateMapdSettings() {
  // Build MapdSettings JSON from individual params
  QJsonObject settings;
  settings["SpeedLimitControlEnabled"] = params.getBool("MapdSpeedLimitControlEnabled");
  settings["MapCurveSpeedControlEnabled"] = params.getBool("MapdMapCurveSpeedControlEnabled");
  settings["VisionCurveSpeedControlEnabled"] = params.getBool("MapdVisionCurveSpeedControlEnabled");

  // Speed limit offset percentage: convert from button index to percentage
  // Button indices: 0=0%, 1=+5%, 2=+10%, 3=+15%
  QString offset_index_str = QString::fromStdString(params.get("MapdSpeedLimitOffsetPercent"));
  int offset_index = offset_index_str.isEmpty() ? 0 : offset_index_str.toInt();

  // Map index to percentage offset
  const int offset_percentages[] = {0, 5, 10, 15};
  int offset_percent = (offset_index >= 0 && offset_index < 4) ? offset_percentages[offset_index] : 0;

  // Store as decimal multiplier (e.g., 10% = 0.10)
  float offset_multiplier = offset_percent / 100.0f;
  settings["SpeedLimitOffsetPercent"] = offset_multiplier;

  // Save to MapdSettings param
  QJsonDocument doc(settings);
  params.put("MapdSettings", doc.toJson(QJsonDocument::Compact).toStdString());
}

void MapdPanel::updateState(const UIState &s) {
  if (!isVisible()) return;

  // Update tile status from mapdOut
  if (s.sm->valid("mapdOut")) {
    auto mapd = (*s.sm)["mapdOut"].getMapdOut();

    // Tile status
    if (mapd.getTileLoaded()) {
      tile_status_lbl->setText(tr("Loaded"));
    } else {
      tile_status_lbl->setText(tr("Not loaded"));
    }
  } else {
    tile_status_lbl->setText(tr("mapd not active"));
  }
}

void MapdPanel::checkForUpdates() {
  // If update is available, perform the update
  if (update_available) {
    performUpdate();
    return;
  }

  // Otherwise, check for updates using mapd_manager.py
  mapd_version_btn->setValue(tr("Checking..."));

  QProcess *check = new QProcess(this);
  check->start("python", QStringList()
    << "/data/openpilot/selfdrive/mapd/mapd_manager.py"
    << "check");

  QObject::connect(check, QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished),
    [=](int exitCode, QProcess::ExitStatus exitStatus) {
      if (exitCode == 0) {
        QString output = check->readAllStandardOutput().trimmed();

        if (output.startsWith("UP_TO_DATE:")) {
          // Already up to date
          mapd_version_btn->setValue(QString::fromStdString(current_mapd_version) + tr(" (latest)"));
          mapd_version_btn->setText(tr("CHECK"));
          update_available = false;
        } else if (output.startsWith("UPDATE_AVAILABLE:")) {
          // Parse: "UPDATE_AVAILABLE: v2.0.2 -> v2.0.3"
          QStringList parts = output.split(" -> ");
          if (parts.size() == 2) {
            QString latest = parts[1].trimmed();
            latest_mapd_version = latest.toStdString();
            mapd_version_btn->setValue(tr("Update available: ") + QString::fromStdString(current_mapd_version) + " → " + latest);
            mapd_version_btn->setText(tr("UPDATE"));
            update_available = true;
          }
        } else {
          mapd_version_btn->setValue(QString::fromStdString(current_mapd_version));
          mapd_version_btn->setText(tr("CHECK"));
        }
      } else {
        mapd_version_btn->setValue(QString::fromStdString(current_mapd_version));
        mapd_version_btn->setText(tr("CHECK"));
      }
      check->deleteLater();
    });
}

void MapdPanel::performUpdate() {
  mapd_version_btn->setValue(tr("Updating..."));
  mapd_version_btn->setText(tr("UPDATING"));

  // Call mapd_manager.py update command
  QProcess *update = new QProcess(this);
  update->start("python", QStringList()
    << "/data/openpilot/selfdrive/mapd/mapd_manager.py"
    << "update");

  QObject::connect(update, QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished),
    [=](int exitCode, QProcess::ExitStatus exitStatus) {
      if (exitCode == 0) {
        // Update successful
        current_mapd_version = latest_mapd_version;

        mapd_version_btn->setValue(QString::fromStdString(current_mapd_version) + tr(" (updated)"));
        mapd_version_btn->setText(tr("CHECK"));
        update_available = false;
      } else {
        // Update failed, reset to check state
        mapd_version_btn->setValue(QString::fromStdString(current_mapd_version));
        mapd_version_btn->setText(tr("CHECK"));
        update_available = false;
      }
      update->deleteLater();
    });
}
