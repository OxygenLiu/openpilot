#include <algorithm>
#include <cassert>
#include <cmath>
#include <string>
#include <tuple>
#include <vector>

#include <QDebug>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QMap>
#include <QProcess>
#include <QProgressDialog>
#include <QRegularExpression>

#include "common/watchdog.h"
#include "common/util.h"
#include "selfdrive/ui/qt/network/networking.h"
#include "selfdrive/ui/qt/offroad/settings.h"
#include "selfdrive/ui/qt/qt_window.h"
#include "selfdrive/ui/qt/widgets/prime.h"
#include "selfdrive/ui/qt/widgets/scrollview.h"
#include "selfdrive/ui/qt/offroad/developer_panel.h"
#include "selfdrive/ui/qt/offroad/firehose.h"

TogglesPanel::TogglesPanel(SettingsWindow *parent) : ListWidget(parent) {
  // param, title, desc, icon, restart needed
  std::vector<std::tuple<QString, QString, QString, QString, bool>> toggle_defs{
    {
      "OpenpilotEnabledToggle",
      tr("Enable openpilot"),
      tr("Use the openpilot system for adaptive cruise control and lane keep driver assistance. Your attention is required at all times to use this feature."),
      "../assets/icons/chffr_wheel.png",
      true,
    },
    {
      "ExperimentalMode",
      tr("Experimental Mode"),
      "",
      "../assets/icons/experimental_white.svg",
      false,
    },
    {
      "DisengageOnAccelerator",
      tr("Disengage on Accelerator Pedal"),
      tr("When enabled, pressing the accelerator pedal will disengage openpilot."),
      "../assets/icons/disengage_on_accelerator.svg",
      false,
    },
    {
      "IsLdwEnabled",
      tr("Enable Lane Departure Warnings"),
      tr("Receive alerts to steer back into the lane when your vehicle drifts over a detected lane line without a turn signal activated while driving over 31 mph (50 km/h)."),
      "../assets/icons/warning.png",
      false,
    },
    {
      "AlwaysOnDM",
      tr("Always-On Driver Monitoring"),
      tr("Enable driver monitoring even when openpilot is not engaged."),
      "../assets/icons/monitoring.png",
      false,
    },
    {
      "RecordFront",
      tr("Record and Upload Driver Camera"),
      tr("Upload data from the driver facing camera and help improve the driver monitoring algorithm."),
      "../assets/icons/monitoring.png",
      true,
    },
    {
      "RecordAudio",
      tr("Record and Upload Microphone Audio"),
      tr("Record and store microphone audio while driving. The audio will be included in the dashcam video in comma connect."),
      "../assets/icons/microphone.png",
      true,
    },
    {
      "IsMetric",
      tr("Use Metric System"),
      tr("Display speed in km/h instead of mph."),
      "../assets/icons/metric.png",
      false,
    },
  };


  std::vector<QString> longi_button_texts{tr("Aggressive"), tr("Standard"), tr("Relaxed")};
  long_personality_setting = new ButtonParamControl("LongitudinalPersonality", tr("Driving Personality"),
                                          tr("Standard is recommended. In aggressive mode, openpilot will follow lead cars closer and be more aggressive with the gas and brake. "
                                             "In relaxed mode openpilot will stay further away from lead cars. On supported cars, you can cycle through these personalities with "
                                             "your steering wheel distance button."),
                                          "../assets/icons/speed_limit.png",
                                          longi_button_texts);

  // set up uiState update for personality setting
  QObject::connect(uiState(), &UIState::uiUpdate, this, &TogglesPanel::updateState);

  for (auto &[param, title, desc, icon, needs_restart] : toggle_defs) {
    auto toggle = new ParamControl(param, title, desc, icon, this);

    bool locked = params.getBool((param + "Lock").toStdString());
    toggle->setEnabled(!locked);

    if (needs_restart && !locked) {
      toggle->setDescription(toggle->getDescription() + tr(" Changing this setting will restart openpilot if the car is powered on."));

      QObject::connect(uiState(), &UIState::engagedChanged, [toggle](bool engaged) {
        toggle->setEnabled(!engaged);
      });

      QObject::connect(toggle, &ParamControl::toggleFlipped, [=](bool state) {
        params.putBool("OnroadCycleRequested", true);
      });
    }

    addItem(toggle);
    toggles[param.toStdString()] = toggle;

    // insert longitudinal personality after NDOG toggle
    if (param == "DisengageOnAccelerator") {
      addItem(long_personality_setting);
    }
  }

  // Toggles with confirmation dialogs
  toggles["ExperimentalMode"]->setActiveIcon("../assets/icons/experimental.svg");
  toggles["ExperimentalMode"]->setConfirmation(true, true);
}

void TogglesPanel::updateState(const UIState &s) {
  const SubMaster &sm = *(s.sm);

  if (sm.updated("selfdriveState")) {
    auto personality = sm["selfdriveState"].getSelfdriveState().getPersonality();
    if (personality != s.scene.personality && s.scene.started && isVisible()) {
      long_personality_setting->setCheckedButton(static_cast<int>(personality));
    }
    uiState()->scene.personality = personality;
  }
}

void TogglesPanel::expandToggleDescription(const QString &param) {
  toggles[param.toStdString()]->showDescription();
}

void TogglesPanel::scrollToToggle(const QString &param) {
  if (auto it = toggles.find(param.toStdString()); it != toggles.end()) {
    auto scroll_area = qobject_cast<QScrollArea*>(parent()->parent());
    if (scroll_area) {
      scroll_area->ensureWidgetVisible(it->second);
    }
  }
}

void TogglesPanel::showEvent(QShowEvent *event) {
  updateToggles();
}

void TogglesPanel::updateToggles() {
  auto experimental_mode_toggle = toggles["ExperimentalMode"];
  const QString e2e_description = QString("%1<br>"
                                          "<h4>%2</h4><br>"
                                          "%3<br>"
                                          "<h4>%4</h4><br>"
                                          "%5<br>")
                                  .arg(tr("openpilot defaults to driving in <b>chill mode</b>. Experimental mode enables <b>alpha-level features</b> that aren't ready for chill mode. Experimental features are listed below:"))
                                  .arg(tr("End-to-End Longitudinal Control"))
                                  .arg(tr("Let the driving model control the gas and brakes. openpilot will drive as it thinks a human would, including stopping for red lights and stop signs. "
                                          "Since the driving model decides the speed to drive, the set speed will only act as an upper bound. This is an alpha quality feature; "
                                          "mistakes should be expected."))
                                  .arg(tr("New Driving Visualization"))
                                  .arg(tr("The driving visualization will transition to the road-facing wide-angle camera at low speeds to better show some turns. The Experimental mode logo will also be shown in the top right corner."));

  const bool is_release = params.getBool("IsReleaseBranch");
  auto cp_bytes = params.get("CarParamsPersistent");
  if (!cp_bytes.empty()) {
    AlignedBuffer aligned_buf;
    capnp::FlatArrayMessageReader cmsg(aligned_buf.align(cp_bytes.data(), cp_bytes.size()));
    cereal::CarParams::Reader CP = cmsg.getRoot<cereal::CarParams>();

    if (hasLongitudinalControl(CP)) {
      // normal description and toggle
      experimental_mode_toggle->setEnabled(true);
      experimental_mode_toggle->setDescription(e2e_description);
      long_personality_setting->setEnabled(true);
    } else {
      // no long for now
      experimental_mode_toggle->setEnabled(false);
      long_personality_setting->setEnabled(false);
      params.remove("ExperimentalMode");

      const QString unavailable = tr("Experimental mode is currently unavailable on this car since the car's stock ACC is used for longitudinal control.");

      QString long_desc = unavailable + " " + \
                          tr("openpilot longitudinal control may come in a future update.");
      if (CP.getAlphaLongitudinalAvailable()) {
        if (is_release) {
          long_desc = unavailable + " " + tr("An alpha version of openpilot longitudinal control can be tested, along with Experimental mode, on non-release branches.");
        } else {
          long_desc = tr("Enable the openpilot longitudinal control (alpha) toggle to allow Experimental mode.");
        }
      }
      experimental_mode_toggle->setDescription("<b>" + long_desc + "</b><br><br>" + e2e_description);
    }

    experimental_mode_toggle->refresh();
  } else {
    experimental_mode_toggle->setDescription(e2e_description);
  }
}

DevicePanel::DevicePanel(SettingsWindow *parent) : ListWidget(parent) {
  setSpacing(50);
  addItem(new LabelControl(tr("Dongle ID"), getDongleId().value_or(tr("N/A"))));
  addItem(new LabelControl(tr("Serial"), params.get("HardwareSerial").c_str()));

  pair_device = new ButtonControl(tr("Pair Device"), tr("PAIR"),
                                  tr("Pair your device with comma connect (connect.comma.ai) and claim your comma prime offer."));
  connect(pair_device, &ButtonControl::clicked, [=]() {
    PairingPopup popup(this);
    popup.exec();
  });
  addItem(pair_device);

  // offroad-only buttons

  auto dcamBtn = new ButtonControl(tr("Driver Camera"), tr("PREVIEW"),
                                   tr("Preview the driver facing camera to ensure that driver monitoring has good visibility. (vehicle must be off)"));
  connect(dcamBtn, &ButtonControl::clicked, [=]() { emit showDriverView(); });
  addItem(dcamBtn);

  resetCalibBtn = new ButtonControl(tr("Reset Calibration"), tr("RESET"), "");
  connect(resetCalibBtn, &ButtonControl::showDescriptionEvent, this, &DevicePanel::updateCalibDescription);
  connect(resetCalibBtn, &ButtonControl::clicked, [&]() {
    if (!uiState()->engaged()) {
      if (ConfirmationDialog::confirm(tr("Are you sure you want to reset calibration?"), tr("Reset"), this)) {
        // Check engaged again in case it changed while the dialog was open
        if (!uiState()->engaged()) {
          params.remove("CalibrationParams");
          params.remove("LiveTorqueParameters");
          params.remove("LiveParameters");
          params.remove("LiveParametersV2");
          params.remove("LiveDelay");
          params.putBool("OnroadCycleRequested", true);
          updateCalibDescription();
        }
      }
    } else {
      ConfirmationDialog::alert(tr("Disengage to Reset Calibration"), this);
    }
  });
  addItem(resetCalibBtn);

  auto retrainingBtn = new ButtonControl(tr("Review Training Guide"), tr("REVIEW"), tr("Review the rules, features, and limitations of openpilot"));
  connect(retrainingBtn, &ButtonControl::clicked, [=]() {
    if (ConfirmationDialog::confirm(tr("Are you sure you want to review the training guide?"), tr("Review"), this)) {
      emit reviewTrainingGuide();
    }
  });
  addItem(retrainingBtn);

  if (Hardware::TICI()) {
    auto regulatoryBtn = new ButtonControl(tr("Regulatory"), tr("VIEW"), "");
    connect(regulatoryBtn, &ButtonControl::clicked, [=]() {
      const std::string txt = util::read_file("../assets/offroad/fcc.html");
      ConfirmationDialog::rich(QString::fromStdString(txt), this);
    });
    addItem(regulatoryBtn);
  }

  auto translateBtn = new ButtonControl(tr("Change Language"), tr("CHANGE"), "");
  connect(translateBtn, &ButtonControl::clicked, [=]() {
    QMap<QString, QString> langs = getSupportedLanguages();
    QString selection = MultiOptionDialog::getSelection(tr("Select a language"), langs.keys(), langs.key(uiState()->language), this);
    if (!selection.isEmpty()) {
      // put language setting, exit Qt UI, and trigger fast restart
      params.put("LanguageSetting", langs[selection].toStdString());
      qApp->exit(18);
      watchdog_kick(0);
    }
  });
  addItem(translateBtn);

  QObject::connect(uiState()->prime_state, &PrimeState::changed, [this] (PrimeState::Type type) {
    pair_device->setVisible(type == PrimeState::PRIME_TYPE_UNPAIRED);
  });
  QObject::connect(uiState(), &UIState::offroadTransition, [=](bool offroad) {
    for (auto btn : findChildren<ButtonControl *>()) {
      if (btn != pair_device && btn != resetCalibBtn) {
        btn->setEnabled(offroad);
      }
    }
  });

  // power buttons
  QHBoxLayout *power_layout = new QHBoxLayout();
  power_layout->setSpacing(30);

  QPushButton *reboot_btn = new QPushButton(tr("Reboot"));
  reboot_btn->setObjectName("reboot_btn");
  power_layout->addWidget(reboot_btn);
  QObject::connect(reboot_btn, &QPushButton::clicked, this, &DevicePanel::reboot);

  QPushButton *poweroff_btn = new QPushButton(tr("Power Off"));
  poweroff_btn->setObjectName("poweroff_btn");
  power_layout->addWidget(poweroff_btn);
  QObject::connect(poweroff_btn, &QPushButton::clicked, this, &DevicePanel::poweroff);

  if (!Hardware::PC()) {
    connect(uiState(), &UIState::offroadTransition, poweroff_btn, &QPushButton::setVisible);
  }

  setStyleSheet(R"(
    #reboot_btn { height: 120px; border-radius: 15px; background-color: #393939; }
    #reboot_btn:pressed { background-color: #4a4a4a; }
    #poweroff_btn { height: 120px; border-radius: 15px; background-color: #E22C2C; }
    #poweroff_btn:pressed { background-color: #FF2424; }
  )");
  addItem(power_layout);
}

void DevicePanel::updateCalibDescription() {
  QString desc = tr("openpilot requires the device to be mounted within 4° left or right and within 5° up or 9° down.");
  std::string calib_bytes = params.get("CalibrationParams");
  if (!calib_bytes.empty()) {
    try {
      AlignedBuffer aligned_buf;
      capnp::FlatArrayMessageReader cmsg(aligned_buf.align(calib_bytes.data(), calib_bytes.size()));
      auto calib = cmsg.getRoot<cereal::Event>().getLiveCalibration();
      if (calib.getCalStatus() != cereal::LiveCalibrationData::Status::UNCALIBRATED) {
        double pitch = calib.getRpyCalib()[1] * (180 / M_PI);
        double yaw = calib.getRpyCalib()[2] * (180 / M_PI);
        desc += tr(" Your device is pointed %1° %2 and %3° %4.")
                    .arg(QString::number(std::abs(pitch), 'g', 1), pitch > 0 ? tr("down") : tr("up"),
                         QString::number(std::abs(yaw), 'g', 1), yaw > 0 ? tr("left") : tr("right"));
      }
    } catch (kj::Exception) {
      qInfo() << "invalid CalibrationParams";
    }
  }

  int lag_perc = 0;
  std::string lag_bytes = params.get("LiveDelay");
  if (!lag_bytes.empty()) {
    try {
      AlignedBuffer aligned_buf;
      capnp::FlatArrayMessageReader cmsg(aligned_buf.align(lag_bytes.data(), lag_bytes.size()));
      lag_perc = cmsg.getRoot<cereal::Event>().getLiveDelay().getCalPerc();
    } catch (kj::Exception) {
      qInfo() << "invalid LiveDelay";
    }
  }
  if (lag_perc < 100) {
    desc += tr("\n\nSteering lag calibration is %1% complete.").arg(lag_perc);
  } else {
    desc += tr("\n\nSteering lag calibration is complete.");
  }

  std::string torque_bytes = params.get("LiveTorqueParameters");
  if (!torque_bytes.empty()) {
    try {
      AlignedBuffer aligned_buf;
      capnp::FlatArrayMessageReader cmsg(aligned_buf.align(torque_bytes.data(), torque_bytes.size()));
      auto torque = cmsg.getRoot<cereal::Event>().getLiveTorqueParameters();
      // don't add for non-torque cars
      if (torque.getUseParams()) {
        int torque_perc = torque.getCalPerc();
        if (torque_perc < 100) {
          desc += tr(" Steering torque response calibration is %1% complete.").arg(torque_perc);
        } else {
          desc += tr(" Steering torque response calibration is complete.");
        }
      }
    } catch (kj::Exception) {
      qInfo() << "invalid LiveTorqueParameters";
    }
  }

  desc += "\n\n";
  desc += tr("openpilot is continuously calibrating, resetting is rarely required. "
             "Resetting calibration will restart openpilot if the car is powered on.");
  resetCalibBtn->setDescription(desc);
}

void DevicePanel::reboot() {
  if (!uiState()->engaged()) {
    if (ConfirmationDialog::confirm(tr("Are you sure you want to reboot?"), tr("Reboot"), this)) {
      // Check engaged again in case it changed while the dialog was open
      if (!uiState()->engaged()) {
        params.putBool("DoReboot", true);
      }
    }
  } else {
    ConfirmationDialog::alert(tr("Disengage to Reboot"), this);
  }
}

void DevicePanel::poweroff() {
  if (!uiState()->engaged()) {
    if (ConfirmationDialog::confirm(tr("Are you sure you want to power off?"), tr("Power Off"), this)) {
      // Check engaged again in case it changed while the dialog was open
      if (!uiState()->engaged()) {
        params.putBool("DoShutdown", true);
      }
    }
  } else {
    ConfirmationDialog::alert(tr("Disengage to Power Off"), this);
  }
}

void SettingsWindow::showEvent(QShowEvent *event) {
  setCurrentPanel(0);
}

void SettingsWindow::setCurrentPanel(int index, const QString &param) {
  if (!param.isEmpty()) {
    // Check if param ends with "Panel" to determine if it's a panel name
    if (param.endsWith("Panel")) {
      QString panelName = param;
      panelName.chop(5); // Remove "Panel" suffix

      // Find the panel by name
      for (int i = 0; i < nav_btns->buttons().size(); i++) {
        if (nav_btns->buttons()[i]->text() == tr(panelName.toStdString().c_str())) {
          index = i;
          break;
        }
      }
    } else {
      emit expandToggleDescription(param);
      emit scrollToToggle(param);
    }
  }

  panel_widget->setCurrentIndex(index);
  nav_btns->buttons()[index]->setChecked(true);
}

SettingsWindow::SettingsWindow(QWidget *parent) : QFrame(parent) {

  // setup two main layouts
  sidebar_widget = new QWidget;
  QVBoxLayout *sidebar_layout = new QVBoxLayout(sidebar_widget);
  panel_widget = new QStackedWidget();

  // close button
  QPushButton *close_btn = new QPushButton(tr("×"));
  close_btn->setStyleSheet(R"(
    QPushButton {
      font-size: 140px;
      padding-bottom: 20px;
      border-radius: 100px;
      background-color: #292929;
      font-weight: 400;
    }
    QPushButton:pressed {
      background-color: #3B3B3B;
    }
  )");
  close_btn->setFixedSize(200, 200);
  sidebar_layout->addSpacing(45);
  sidebar_layout->addWidget(close_btn, 0, Qt::AlignCenter);
  QObject::connect(close_btn, &QPushButton::clicked, this, &SettingsWindow::closeSettings);

  // setup panels
  DevicePanel *device = new DevicePanel(this);
  QObject::connect(device, &DevicePanel::reviewTrainingGuide, this, &SettingsWindow::reviewTrainingGuide);
  QObject::connect(device, &DevicePanel::showDriverView, this, &SettingsWindow::showDriverView);

  TogglesPanel *toggles = new TogglesPanel(this);
  QObject::connect(this, &SettingsWindow::expandToggleDescription, toggles, &TogglesPanel::expandToggleDescription);
  QObject::connect(this, &SettingsWindow::scrollToToggle, toggles, &TogglesPanel::scrollToToggle);

  auto networking = new Networking(this);
  QObject::connect(uiState()->prime_state, &PrimeState::changed, networking, &Networking::setPrimeType);

  // Forward network connectivity signal from Networking to SettingsWindow
  QObject::connect(networking, &Networking::connectivityChanged, this, &SettingsWindow::networkConnectivityChanged);

  auto vehiclePanel = new VehiclePanel(this);
  // Connect network connectivity to vehicle panel for UPDATE button enable/disable
  QObject::connect(this, &SettingsWindow::networkConnectivityChanged, vehiclePanel, &VehiclePanel::onNetworkConnectivityChanged);

  QList<QPair<QString, QWidget *>> panels = {
    {tr("Device"), device},
    {tr("Network"), networking},
    {tr("Toggles"), toggles},
    {tr("Models"), vehiclePanel},
    {tr("Software"), new SoftwarePanel(this)},
    {tr("Developer"), new DeveloperPanel(this)},
  };

  nav_btns = new QButtonGroup(this);
  for (auto &[name, panel] : panels) {
    QPushButton *btn = new QPushButton(name);
    btn->setCheckable(true);
    btn->setChecked(nav_btns->buttons().size() == 0);
    btn->setStyleSheet(R"(
      QPushButton {
        color: grey;
        border: none;
        background: none;
        font-size: 65px;
        font-weight: 500;
      }
      QPushButton:checked {
        color: white;
      }
      QPushButton:pressed {
        color: #ADADAD;
      }
    )");
    btn->setSizePolicy(QSizePolicy::Preferred, QSizePolicy::Expanding);
    nav_btns->addButton(btn);
    sidebar_layout->addWidget(btn, 0, Qt::AlignRight);

    const int lr_margin = name != tr("Network") ? 50 : 0;  // Network panel handles its own margins
    panel->setContentsMargins(lr_margin, 25, lr_margin, 25);

    ScrollView *panel_frame = new ScrollView(panel, this);
    panel_widget->addWidget(panel_frame);

    QObject::connect(btn, &QPushButton::clicked, [=, w = panel_frame]() {
      btn->setChecked(true);
      panel_widget->setCurrentWidget(w);
    });
  }
  sidebar_layout->setContentsMargins(50, 50, 100, 50);

  // main settings layout, sidebar + main panel
  QHBoxLayout *main_layout = new QHBoxLayout(this);

  sidebar_widget->setFixedWidth(500);
  main_layout->addWidget(sidebar_widget);
  main_layout->addWidget(panel_widget);

  setStyleSheet(R"(
    * {
      color: white;
      font-size: 50px;
    }
    SettingsWindow {
      background-color: black;
    }
    QStackedWidget, ScrollView {
      background-color: #292929;
      border-radius: 30px;
    }
  )");
}

VehiclePanel::VehiclePanel(SettingsWindow *parent) : ListWidget(parent) {
  // Driving model selector button
  driving_model_selector_btn = new ButtonControl(tr("Driving Model"), tr("SELECT"));
  QObject::connect(driving_model_selector_btn, &ButtonControl::clicked, this, &VehiclePanel::openDrivingModelSelector);
  addItem(driving_model_selector_btn);

  // Driver monitoring model selector button
  dm_model_selector_btn = new ButtonControl(tr("DM Model"), tr("SELECT"));
  QObject::connect(dm_model_selector_btn, &ButtonControl::clicked, this, &VehiclePanel::openDMModelSelector);
  addItem(dm_model_selector_btn);

  // Update registry / Download models button
  download_models_btn = new ButtonControl(tr("Model Updates"), tr("UPDATE"));
  QObject::connect(download_models_btn, &ButtonControl::clicked, this, &VehiclePanel::updateRegistryOrDownload);
  addItem(download_models_btn);
  download_models_btn->setValue(tr("No Network"));  // Default text when offline
  download_models_btn->setEnabled(false);  // Disabled until network connectivity confirmed

  // Update model button text with active models
  updateModelButtonText();
}

void VehiclePanel::onNetworkConnectivityChanged(bool connected) {
  network_connected = connected;

  // Enable/disable UPDATE button based on GitHub connectivity
  if (!models_ready_to_download) {
    // Only update button state if not in "Download (X new)" mode
    download_models_btn->setEnabled(connected);
    download_models_btn->setValue(connected ? tr("Check GitHub") : tr("No Network"));
  }
  // If models are ready to download, keep button enabled regardless of connectivity
}

void VehiclePanel::openDrivingModelSelector() {
  const QString script_path = "/data/openpilot/selfdrive/modeld/model_swapper.py";

  while (true) {
    // Get available driving models with dates
    QProcess process;
    process.start("python3", QStringList() << script_path << "--type" << "driving" << "list-with-dates");
    process.waitForFinished(5000);

    QString output = process.readAllStandardOutput();
    QStringList models = output.split('\n', QString::SkipEmptyParts);

    if (models.isEmpty()) {
      return;
    }

    // Get current active model (returns name without date)
    QProcess active_process;
    active_process.start("python3", QStringList() << script_path << "--type" << "driving" << "active");
    active_process.waitForFinished(3000);
    QString active_name = active_process.readAllStandardOutput().trimmed();

    // Find the matching entry in models list (which includes dates)
    QString current_with_date = active_name;  // Default fallback
    QRegularExpression date_pattern(" \\(\\d{4}-\\d{2}-\\d{2}\\)$");
    for (const QString &model : models) {
      QString model_name = QString(model).remove(date_pattern);
      if (model_name == active_name) {
        current_with_date = model;
        break;
      }
    }

    // Show selection dialog with properly formatted active model
    QString selection = MultiOptionDialog::getSelection(tr("Select a driving model"), models, current_with_date, this);
    if (selection.isEmpty()) {
      // User cancelled
      return;
    }

    // Extract model name without date (format: "Name (YYYY-MM-DD)")
    QString model_name = QString(selection).remove(date_pattern);

    // Ask what to do with selected model
    QStringList actions;
    if (selection != current_with_date) {
      actions << tr("Activate") << tr("Delete") << tr("Cancel");
    } else {
      // Can't activate or delete active model - only cancel
      actions << tr("Cancel");
    }

    QString action = MultiOptionDialog::getSelection(
      tr("What would you like to do with") + "\n" + selection + "?",
      actions, "", this);

    if (action == tr("Activate")) {
      // Swap to selected model (use name without date)
      QProcess swap_process;
      swap_process.start("python3", QStringList() << script_path << "--type" << "driving" << "swap" << model_name);
      swap_process.waitForFinished(10000);

      // Update button value with date format
      driving_model_selector_btn->setValue(selection);

      // Prompt for restart
      if (ConfirmationDialog::confirm(tr("Model swapped successfully. Restart openpilot now?"), tr("Restart"), this)) {
        params.putBool("DoReboot", true);
      }
      return;
    } else if (action == tr("Delete")) {
      // Confirm deletion
      if (ConfirmationDialog::confirm(tr("Delete") + " " + selection + "?", tr("Delete"), this)) {
        QProcess delete_process;
        delete_process.start("python3", QStringList() << script_path << "--type" << "driving" << "delete" << model_name);
        delete_process.waitForFinished(10000);

        QString error = delete_process.readAllStandardError();
        if (!error.isEmpty()) {
          ConfirmationDialog::alert(error, this);
        }
        // Continue loop to show updated list
      }
    } else {
      // Cancel or empty - return to settings
      return;
    }
  }
}

void VehiclePanel::openDMModelSelector() {
  const QString script_path = "/data/openpilot/selfdrive/modeld/model_swapper.py";

  while (true) {
    // Get available DM models with dates
    QProcess process;
    process.start("python3", QStringList() << script_path << "--type" << "dm" << "list-with-dates");
    process.waitForFinished(5000);

    QString output = process.readAllStandardOutput();
    QStringList models = output.split('\n', QString::SkipEmptyParts);

    if (models.isEmpty()) {
      return;
    }

    // Get current active model (returns name without date)
    QProcess active_process;
    active_process.start("python3", QStringList() << script_path << "--type" << "dm" << "active");
    active_process.waitForFinished(3000);
    QString active_name = active_process.readAllStandardOutput().trimmed();

    // Find the matching entry in models list (which includes dates)
    QString current_with_date = active_name;  // Default fallback
    QRegularExpression date_pattern(" \\(\\d{4}-\\d{2}-\\d{2}\\)$");
    for (const QString &model : models) {
      QString model_name = QString(model).remove(date_pattern);
      if (model_name == active_name) {
        current_with_date = model;
        break;
      }
    }

    // Show selection dialog with properly formatted active model
    QString selection = MultiOptionDialog::getSelection(tr("Select a DM model"), models, current_with_date, this);
    if (selection.isEmpty()) {
      // User cancelled
      return;
    }

    // Extract model name without date (format: "Name (YYYY-MM-DD)")
    QString model_name = QString(selection).remove(date_pattern);

    // Ask what to do with selected model
    QStringList actions;
    if (selection != current_with_date) {
      actions << tr("Activate") << tr("Delete") << tr("Cancel");
    } else {
      // Can't select or delete active model
      actions << tr("Delete") << tr("Cancel");
    }

    QString action = MultiOptionDialog::getSelection(
      tr("What would you like to do with") + "\n" + selection + "?",
      actions, "", this);

    if (action == tr("Activate")) {
      // Swap to selected model (use name without date)
      QProcess swap_process;
      swap_process.start("python3", QStringList() << script_path << "--type" << "dm" << "swap" << model_name);
      swap_process.waitForFinished(10000);

      // Update button value with date format
      dm_model_selector_btn->setValue(selection);

      // Prompt for restart
      if (ConfirmationDialog::confirm(tr("Model swapped successfully. Restart openpilot now?"), tr("Restart"), this)) {
        params.putBool("DoReboot", true);
      }
      return;
    } else if (action == tr("Delete")) {
      // Confirm deletion
      if (ConfirmationDialog::confirm(tr("Delete") + " " + selection + "?", tr("Delete"), this)) {
        QProcess delete_process;
        delete_process.start("python3", QStringList() << script_path << "--type" << "dm" << "delete" << model_name);
        delete_process.waitForFinished(10000);

        QString error = delete_process.readAllStandardError();
        if (!error.isEmpty()) {
          ConfirmationDialog::alert(error, this);
        }
        // Continue loop to show updated list
      }
    } else {
      // Cancel or empty - return to settings
      return;
    }
  }
}

void VehiclePanel::updateModelButtonText() {
  const QString script_path = "/data/openpilot/selfdrive/modeld/model_swapper.py";
  QRegularExpression date_pattern(" \\(\\d{4}-\\d{2}-\\d{2}\\)$");

  // Update driving model button with date
  QProcess driving_active;
  driving_active.start("python3", QStringList() << script_path << "--type" << "driving" << "active");
  if (driving_active.waitForFinished(3000)) {
    QString active_name = driving_active.readAllStandardOutput().trimmed();
    if (!active_name.isEmpty() && active_name != "No active driving model") {
      // Get list with dates and find matching entry
      QProcess driving_list;
      driving_list.start("python3", QStringList() << script_path << "--type" << "driving" << "list-with-dates");
      if (driving_list.waitForFinished(3000)) {
        QString output = driving_list.readAllStandardOutput();
        QStringList models = output.split('\n', QString::SkipEmptyParts);
        QString model_with_date = active_name;  // Fallback
        for (const QString &model : models) {
          QString model_name = QString(model).remove(date_pattern);
          if (model_name == active_name) {
            model_with_date = model;
            break;
          }
        }
        driving_model_selector_btn->setValue(model_with_date);
      }
    }
  }

  // Update DM model button with date
  QProcess dm_active;
  dm_active.start("python3", QStringList() << script_path << "--type" << "dm" << "active");
  if (dm_active.waitForFinished(3000)) {
    QString active_name = dm_active.readAllStandardOutput().trimmed();
    if (!active_name.isEmpty() && active_name != "No active dm model") {
      // Get list with dates and find matching entry
      QProcess dm_list;
      dm_list.start("python3", QStringList() << script_path << "--type" << "dm" << "list-with-dates");
      if (dm_list.waitForFinished(3000)) {
        QString output = dm_list.readAllStandardOutput();
        QStringList models = output.split('\n', QString::SkipEmptyParts);
        QString model_with_date = active_name;  // Fallback
        for (const QString &model : models) {
          QString model_name = QString(model).remove(date_pattern);
          if (model_name == active_name) {
            model_with_date = model;
            break;
          }
        }
        dm_model_selector_btn->setValue(model_with_date);
      }
    }
  }
}

void VehiclePanel::updateRegistryOrDownload() {
  // If models not yet ready, update registry from GitHub first
  if (!models_ready_to_download) {
    // Show progress
    download_models_btn->setValue(tr("Checking..."));
    download_models_btn->setEnabled(false);  // Disable button during update

    // Clear previous status
    params.remove("ModelUpdateStatus");
    params.remove("ModelUpdateResults");
    params.remove("ModelUpdateError");

    // Launch async Python script (fire and forget - no Qt process management!)
    QProcess::startDetached("python3", QStringList()
      << "/data/openpilot/selfdrive/modeld/update_models_async.py");

    // Start polling Params for status updates
    if (!update_timer) {
      update_timer = new QTimer(this);
      connect(update_timer, &QTimer::timeout, this, &VehiclePanel::checkUpdateStatus);
    }
    update_poll_count = 0;  // Reset timeout counter
    update_timer->start(1000);  // Poll every 1 second
    return;
  }

  // Otherwise, models are ready - proceed with download
  downloadNewModels();
}

void VehiclePanel::checkUpdateStatus() {
  // Increment poll counter and check for timeout
  update_poll_count++;
  if (update_poll_count >= 60) {  // 60 seconds timeout (60 polls × 1 second)
    update_timer->stop();
    download_models_btn->setValue(tr("Check GitHub"));
    download_models_btn->setEnabled(true);
    ConfirmationDialog::alert(tr("Update check timed out after 60 seconds. Please try again."), this);
    return;
  }

  // Check Params for update status
  std::string status = params.get("ModelUpdateStatus");

  if (status.empty()) {
    // Still waiting for script to start
    return;
  }

  if (status == "checking") {
    // Still in progress
    return;
  }

  // Update complete or error - stop polling
  update_timer->stop();
  download_models_btn->setEnabled(true);

  if (status == "complete") {
    // Parse results
    std::string results_json = params.get("ModelUpdateResults");
    QJsonDocument doc = QJsonDocument::fromJson(QByteArray::fromStdString(results_json));

    if (!doc.isNull() && doc.isObject()) {
      QJsonObject obj = doc.object();
      int total = obj["total"].toInt();

      if (total > 0) {
        // Models found - change button to download mode
        models_ready_to_download = true;
        download_models_btn->setValue(QString(tr("Download (%1 new)")).arg(total));
      } else {
        // No new models
        models_ready_to_download = false;
        download_models_btn->setValue(tr("Check GitHub"));
        ConfirmationDialog::alert(tr("No new models available"), this);
      }
    } else {
      download_models_btn->setValue(tr("Check GitHub"));
      ConfirmationDialog::alert(tr("Failed to parse results"), this);
    }
  } else if (status == "error") {
    // Show error
    std::string error = params.get("ModelUpdateError");
    download_models_btn->setValue(tr("Check GitHub"));
    ConfirmationDialog::alert(QString::fromStdString(error), this);
  }
}

void VehiclePanel::downloadNewModels() {
  // Get list of new models
  const QString script_path = "/data/openpilot/selfdrive/modeld/download_openpilot_models.py";

  QProcess process;
  process.start("python3", QStringList() << script_path << "check-updates");
  if (!process.waitForFinished(5000)) {
    ConfirmationDialog::alert(tr("Failed to check for updates"), this);
    return;
  }

  QString output = process.readAllStandardOutput();
  QJsonDocument doc = QJsonDocument::fromJson(output.toUtf8());

  if (doc.isNull() || !doc.isObject()) {
    ConfirmationDialog::alert(tr("Invalid update data"), this);
    return;
  }

  QJsonObject obj = doc.object();
  QJsonArray driving_models = obj["driving"].toArray();
  QJsonArray dm_models = obj["dm"].toArray();

  if (driving_models.isEmpty() && dm_models.isEmpty()) {
    ConfirmationDialog::alert(tr("No new models available"), this);
    return;
  }

  // Build selection list with formatted names
  // Only show models compatible with v0.10.1 (after Firehose PR #36087)
  // Exclude already downloaded models and sort by date (newest first)
  QList<QJsonObject> filtered_models;
  const int MIN_COMPATIBLE_PR = 36087;  // Firehose model - first v0.10.1 compatible

  // Collect and filter driving models
  for (const QJsonValue &val : driving_models) {
    QJsonObject model_obj = val.toObject();

    // Filter 1: only show models after Firehose (PR #36087) for v0.10.1 compatibility
    int pr_number = model_obj["pr"].toInt();
    if (pr_number > 0 && pr_number <= MIN_COMPATIBLE_PR) {
      continue;  // Skip incompatible models
    }

    // Filter 2: exclude already downloaded models
    bool is_downloaded = model_obj["downloaded"].toBool();
    if (is_downloaded) {
      continue;  // Skip already downloaded models
    }

    // Filter 3: exclude reverted models
    bool is_reverted = model_obj["reverted"].toBool();
    if (is_reverted) {
      continue;  // Skip reverted models
    }

    model_obj["emoji"] = "🚗";  // Mark as driving model
    filtered_models.append(model_obj);
  }

  // Collect and filter DM models
  for (const QJsonValue &val : dm_models) {
    QJsonObject model_obj = val.toObject();

    // Filter 1: only show models after Firehose (PR #36087) for v0.10.1 compatibility
    int pr_number = model_obj["pr"].toInt();
    if (pr_number > 0 && pr_number <= MIN_COMPATIBLE_PR) {
      continue;  // Skip incompatible models
    }

    // Filter 2: exclude already downloaded models
    bool is_downloaded = model_obj["downloaded"].toBool();
    if (is_downloaded) {
      continue;  // Skip already downloaded models
    }

    // Filter 3: exclude reverted models
    bool is_reverted = model_obj["reverted"].toBool();
    if (is_reverted) {
      continue;  // Skip reverted models
    }

    model_obj["emoji"] = "👁️";  // Mark as DM model
    filtered_models.append(model_obj);
  }

  // Sort by date in descending order (newest first)
  std::sort(filtered_models.begin(), filtered_models.end(), [](const QJsonObject &a, const QJsonObject &b) {
    return a["date"].toString() > b["date"].toString();
  });

  // Build display list from sorted models
  QStringList model_display_list;
  QMap<QString, QJsonObject> model_map;  // Map display name to model data

  for (const QJsonObject &model_obj : filtered_models) {
    QString emoji = model_obj["emoji"].toString();
    QString name = model_obj["name"].toString();
    QString date = model_obj["date"].toString();
    QString display = QString("%1 %2 (%3)").arg(emoji).arg(name).arg(date);
    model_display_list << display;
    model_map[display] = model_obj;
  }

  // Show selection dialog (like model selector)
  QString selection = MultiOptionDialog::getSelection(
    tr("Select model to download"),
    model_display_list,
    "",  // No current selection
    this);

  if (selection.isEmpty()) {
    return;  // User cancelled
  }

  // Get selected model data
  QJsonObject selected_model = model_map[selection];
  QString model_id = selected_model["id"].toString();
  QString model_name = selected_model["name"].toString();
  QString model_type = selected_model["type"].toString();

  // Create progress dialog
  QProgressDialog progress(tr("Downloading %1...").arg(model_name), tr("Cancel"), 0, 1, this);
  progress.setWindowModality(Qt::WindowModal);
  progress.setMinimumDuration(0);
  progress.setValue(0);

  // Download the selected model
  QProcess download_process;
  download_process.start("python3", QStringList() << script_path << "download" << model_id << "--type" << model_type);

  if (!download_process.waitForFinished(120000)) {  // 2 minute timeout
    ConfirmationDialog::alert(tr("Download timeout for %1").arg(model_name), this);
    return;
  }

  progress.setValue(1);

  if (download_process.exitCode() != 0) {
    QString error = download_process.readAllStandardError();
    ConfirmationDialog::alert(tr("Download failed for %1:\n%2").arg(model_name).arg(error), this);
    return;
  }

  // Success - reset button to check GitHub again
  models_ready_to_download = false;
  download_models_btn->setValue(tr("Check GitHub"));

  // Return to panel automatically (no success alert)
}
