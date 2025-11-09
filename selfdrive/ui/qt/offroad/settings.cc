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

  QList<QPair<QString, QWidget *>> panels = {
    {tr("Device"), device},
    {tr("Network"), networking},
    {tr("Toggles"), toggles},
    {tr("Vehicle"), new VehiclePanel(this)},
    {tr("Software"), new SoftwarePanel(this)},
    {tr("Firehose"), new FirehosePanel(this)},
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
  // Vehicle information label
  vehicle_info_lbl = new LabelControl(tr("Vehicle Information"), "");
  addItem(vehicle_info_lbl);

  // BMW Vitals Display (Coolant, Oil, Battery with individual colors)
  bmw_vitals_widget = new QWidget(this);
  QHBoxLayout *vitals_layout = new QHBoxLayout(bmw_vitals_widget);
  vitals_layout->setMargin(0);
  vitals_layout->setSpacing(10);

  // Title label
  QLabel *vitals_title = new QLabel(tr("BMW Vitals"), bmw_vitals_widget);
  vitals_title->setStyleSheet("font-size: 50px; font-weight: 400;");
  vitals_layout->addWidget(vitals_title, 1);

  // Create three separate value labels for individual coloring
  coolant_lbl = new QLabel("--", bmw_vitals_widget);
  coolant_lbl->setStyleSheet("font-size: 40px; color: #aaaaaa;");
  coolant_lbl->setAlignment(Qt::AlignRight | Qt::AlignVCenter);
  vitals_layout->addWidget(coolant_lbl);

  QLabel *separator1 = new QLabel(" | ", bmw_vitals_widget);
  separator1->setStyleSheet("font-size: 40px; color: #666666;");
  vitals_layout->addWidget(separator1);

  oil_lbl = new QLabel("--", bmw_vitals_widget);
  oil_lbl->setStyleSheet("font-size: 40px; color: #aaaaaa;");
  oil_lbl->setAlignment(Qt::AlignRight | Qt::AlignVCenter);
  vitals_layout->addWidget(oil_lbl);

  QLabel *separator2 = new QLabel(" | ", bmw_vitals_widget);
  separator2->setStyleSheet("font-size: 40px; color: #666666;");
  vitals_layout->addWidget(separator2);

  battery_lbl = new QLabel("--", bmw_vitals_widget);
  battery_lbl->setStyleSheet("font-size: 40px; color: #aaaaaa;");
  battery_lbl->setAlignment(Qt::AlignRight | Qt::AlignVCenter);
  vitals_layout->addWidget(battery_lbl);

  bmw_vitals_widget->setFixedHeight(120);
  addItem(bmw_vitals_widget);

  // Personalized longitudinal learning display
  personalized_learning_lbl = new LabelControl(tr("Personalized Following"), tr("Not learning"));
  addItem(personalized_learning_lbl);

  // Details button for learned scales (only shown when data is learned)
  personalized_details_btn = new ButtonControl(tr("View Learned Scales"), tr("View personalized T_FOLLOW scales"));
  QObject::connect(personalized_details_btn, &ButtonControl::clicked, this, &VehiclePanel::openPersonalizedDetails);
  addItem(personalized_details_btn);
  personalized_details_btn->setVisible(false);  // Initially hidden

  // Longitudinal actuator delay display
  longitudinal_delay_lbl = new LabelControl(tr("Longitudinal Delay"), tr("Not estimated"));
  addItem(longitudinal_delay_lbl);

  // Details button for longitudinal delay (only shown when data is estimated)
  longitudinal_delay_details_btn = new ButtonControl(tr("View Delay Details"), tr("View longitudinal actuator delay estimation"));
  QObject::connect(longitudinal_delay_details_btn, &ButtonControl::clicked, this, &VehiclePanel::openLongitudinalDelayDetails);
  addItem(longitudinal_delay_details_btn);
  longitudinal_delay_details_btn->setVisible(false);  // Initially hidden

  // Lateral actuator delay display
  lateral_delay_lbl = new LabelControl(tr("Lateral Delay"), tr("Not estimated"));
  addItem(lateral_delay_lbl);

  // Details button for lateral delay (only shown when data is estimated)
  lateral_delay_details_btn = new ButtonControl(tr("View Delay Details"), tr("View lateral actuator delay estimation"));
  QObject::connect(lateral_delay_details_btn, &ButtonControl::clicked, this, &VehiclePanel::openLateralDelayDetails);
  addItem(lateral_delay_details_btn);
  lateral_delay_details_btn->setVisible(false);  // Initially hidden

  // Curve speed control learning display
  curve_speed_lbl = new LabelControl(tr("Curve Speed Control"), tr("Not learning"));
  addItem(curve_speed_lbl);

  // Details button for curve speed (only shown when data is learning or learned)
  curve_speed_details_btn = new ButtonControl(tr("View Parameters"), tr("View learned curve speed parameters"));
  QObject::connect(curve_speed_details_btn, &ButtonControl::clicked, this, &VehiclePanel::openCurveSpeedDetails);
  addItem(curve_speed_details_btn);
  curve_speed_details_btn->setVisible(false);  // Initially hidden

  // Driving model selector button
  driving_model_selector_btn = new ButtonControl(tr("Driving Model"), tr("SELECT"));
  QObject::connect(driving_model_selector_btn, &ButtonControl::clicked, this, &VehiclePanel::openDrivingModelSelector);
  addItem(driving_model_selector_btn);

  // Driver monitoring model selector button
  dm_model_selector_btn = new ButtonControl(tr("DM Model"), tr("SELECT"));
  QObject::connect(dm_model_selector_btn, &ButtonControl::clicked, this, &VehiclePanel::openDMModelSelector);
  addItem(dm_model_selector_btn);

  // Download models button (hidden until updates available)
  download_models_btn = new ButtonControl(tr("Download Models"), tr("DOWNLOAD"));
  QObject::connect(download_models_btn, &ButtonControl::clicked, this, &VehiclePanel::checkAndDownloadModels);
  addItem(download_models_btn);
  download_models_btn->setVisible(false);  // Hidden by default

  // Set up UI state updates to show/hide BMW-specific controls
  QObject::connect(uiState(), &UIState::uiUpdate, this, &VehiclePanel::updateState);

  // Initial update
  updateVehicleInfo();

  // Check for model updates
  checkForModelUpdates();
}

void VehiclePanel::openPersonalizedDetails() {
  const UIState &s = *uiState();

  // Create dialog
  QDialog *dialog = new QDialog(this);
  dialog->setWindowTitle(tr("Personalized T_FOLLOW Scales"));
  dialog->setStyleSheet("QDialog { background-color: #292929; }");

  QVBoxLayout *main_layout = new QVBoxLayout(dialog);
  main_layout->setContentsMargins(50, 50, 50, 50);
  main_layout->setSpacing(30);

  // Title
  QLabel *title = new QLabel(tr("Learned Following Distances"), dialog);
  title->setStyleSheet("QLabel { font-size: 48px; font-weight: bold; color: white; }");
  title->setAlignment(Qt::AlignCenter);
  main_layout->addWidget(title);

  // VREL interval labels
  const char* interval_labels[5] = {
    "0-10 kph",
    "10-20 kph",
    "20-30 kph",
    "30-40 kph",
    "40+ kph"
  };

  // Create table-like display
  for (int i = 0; i < 5; i++) {
    QFrame *interval_frame = new QFrame(dialog);
    interval_frame->setStyleSheet("QFrame { background-color: #1A1A1A; border-radius: 10px; padding: 20px; }");

    QHBoxLayout *interval_layout = new QHBoxLayout(interval_frame);

    // Interval label
    QLabel *interval_lbl = new QLabel(tr(interval_labels[i]), interval_frame);
    interval_lbl->setStyleSheet("QLabel { font-size: 32px; color: white; }");
    interval_layout->addWidget(interval_lbl);

    interval_layout->addStretch();

    // Scale value
    float scale = s.scene.personalized_scales[i];
    float t_follow = scale * 1.45;  // Baseline is 1.45s
    QLabel *scale_lbl = new QLabel(QString("%1x (%2s)").arg(scale, 0, 'f', 2).arg(t_follow, 0, 'f', 2), interval_frame);
    scale_lbl->setStyleSheet("QLabel { font-size: 36px; font-weight: bold; color: #5CB85C; }");
    interval_layout->addWidget(scale_lbl);

    interval_layout->addSpacing(40);

    // Confidence indicator (valid blocks)
    uint16_t blocks = s.scene.personalized_valid_blocks[i];
    QString confidence_text = QString("%1/10").arg(blocks);
    QString confidence_color = blocks >= 10 ? "#5CB85C" : (blocks >= 5 ? "#DAB825" : "#E22C2C");
    QLabel *confidence_lbl = new QLabel(confidence_text, interval_frame);
    confidence_lbl->setStyleSheet(QString("QLabel { font-size: 28px; color: %1; }").arg(confidence_color));
    interval_layout->addWidget(confidence_lbl);

    main_layout->addWidget(interval_frame);
  }

  // Explanation text
  QLabel *explanation = new QLabel(tr("Scale factors multiply the baseline T_FOLLOW (1.45s) based on approach speed"), dialog);
  explanation->setStyleSheet("QLabel { font-size: 24px; color: #999; }");
  explanation->setAlignment(Qt::AlignCenter);
  explanation->setWordWrap(true);
  main_layout->addWidget(explanation);

  // Close button
  QPushButton *close_btn = new QPushButton(tr("Close"), dialog);
  close_btn->setStyleSheet("QPushButton { font-size: 36px; padding: 20px; background-color: #5CB85C; color: white; border-radius: 10px; }");
  QObject::connect(close_btn, &QPushButton::clicked, dialog, &QDialog::accept);
  main_layout->addWidget(close_btn);

  dialog->setMinimumSize(1000, 800);
  dialog->exec();
  delete dialog;
}

void VehiclePanel::openLongitudinalDelayDetails() {
  const UIState &s = *uiState();

  // Create dialog
  QDialog *dialog = new QDialog(this);
  dialog->setWindowTitle(tr("Longitudinal Actuator Delay"));
  dialog->setStyleSheet("QDialog { background-color: #292929; }");

  QVBoxLayout *main_layout = new QVBoxLayout(dialog);
  main_layout->setContentsMargins(50, 50, 50, 50);
  main_layout->setSpacing(30);

  // Title
  QLabel *title = new QLabel(tr("Longitudinal Actuator Delay Estimation"), dialog);
  title->setStyleSheet("QLabel { font-size: 48px; font-weight: bold; color: white; }");
  title->setAlignment(Qt::AlignCenter);
  main_layout->addWidget(title);

  // Status indicator
  QString status_text;
  QString status_color;
  if (s.scene.longitudinal_status == 1) {  // Estimated
    status_text = tr("ESTIMATED");
    status_color = "#5CB85C";  // Green
  } else if (s.scene.longitudinal_status == 2) {  // Invalid
    status_text = tr("INVALID");
    status_color = "#D9534F";  // Red
  } else {  // Unestimated
    status_text = tr("NOT ESTIMATED");
    status_color = "#999";  // Gray
  }

  QLabel *status_lbl = new QLabel(status_text, dialog);
  status_lbl->setStyleSheet(QString("QLabel { font-size: 36px; font-weight: bold; color: %1; }").arg(status_color));
  status_lbl->setAlignment(Qt::AlignCenter);
  main_layout->addWidget(status_lbl);

  // Estimated delay value
  QFrame *delay_frame = new QFrame(dialog);
  delay_frame->setStyleSheet("QFrame { background-color: #1E1E1E; border-radius: 15px; padding: 20px; }");
  QVBoxLayout *delay_layout = new QVBoxLayout(delay_frame);

  QLabel *delay_label = new QLabel(tr("Estimated Delay"), dialog);
  delay_label->setStyleSheet("QLabel { font-size: 28px; color: #999; }");
  delay_label->setAlignment(Qt::AlignCenter);
  delay_layout->addWidget(delay_label);

  QString delay_text = QString("%1 s").arg(s.scene.longitudinal_delay_estimate, 0, 'f', 2);
  QLabel *delay_value = new QLabel(delay_text, dialog);
  delay_value->setStyleSheet("QLabel { font-size: 56px; font-weight: bold; color: white; }");
  delay_value->setAlignment(Qt::AlignCenter);
  delay_layout->addWidget(delay_value);

  main_layout->addWidget(delay_frame);

  // Standard deviation
  QFrame *std_frame = new QFrame(dialog);
  std_frame->setStyleSheet("QFrame { background-color: #1E1E1E; border-radius: 15px; padding: 20px; }");
  QVBoxLayout *std_layout = new QVBoxLayout(std_frame);

  QLabel *std_label = new QLabel(tr("Standard Deviation"), dialog);
  std_label->setStyleSheet("QLabel { font-size: 28px; color: #999; }");
  std_label->setAlignment(Qt::AlignCenter);
  std_layout->addWidget(std_label);

  QString std_text = QString("± %1 s").arg(s.scene.longitudinal_delay_std, 0, 'f', 2);
  QLabel *std_value = new QLabel(std_text, dialog);
  std_value->setStyleSheet("QLabel { font-size: 42px; font-weight: bold; color: white; }");
  std_value->setAlignment(Qt::AlignCenter);
  std_layout->addWidget(std_value);

  main_layout->addWidget(std_frame);

  // Calibration progress
  QFrame *cal_frame = new QFrame(dialog);
  cal_frame->setStyleSheet("QFrame { background-color: #1E1E1E; border-radius: 15px; padding: 20px; }");
  QHBoxLayout *cal_layout = new QHBoxLayout(cal_frame);

  QLabel *cal_label = new QLabel(tr("Calibration:"), dialog);
  cal_label->setStyleSheet("QLabel { font-size: 32px; color: #999; }");
  cal_layout->addWidget(cal_label);

  QString cal_text = QString("%1% (%2/%3 blocks)")
                        .arg(s.scene.longitudinal_cal_perc)
                        .arg(s.scene.longitudinal_valid_blocks)
                        .arg(10);  // BLOCK_NUM_NEEDED from lagd.py
  QLabel *cal_value = new QLabel(cal_text, dialog);
  cal_value->setStyleSheet("QLabel { font-size: 32px; font-weight: bold; color: white; }");
  cal_layout->addWidget(cal_value);
  cal_layout->addStretch();

  main_layout->addWidget(cal_frame);

  // Explanation text
  QLabel *explanation = new QLabel(tr("Longitudinal actuator delay is the time between commanded acceleration and actual vehicle response"), dialog);
  explanation->setStyleSheet("QLabel { font-size: 24px; color: #999; }");
  explanation->setAlignment(Qt::AlignCenter);
  explanation->setWordWrap(true);
  main_layout->addWidget(explanation);

  // Close button
  QPushButton *close_btn = new QPushButton(tr("Close"), dialog);
  close_btn->setStyleSheet("QPushButton { font-size: 36px; padding: 20px; background-color: #5CB85C; color: white; border-radius: 10px; }");
  QObject::connect(close_btn, &QPushButton::clicked, dialog, &QDialog::accept);
  main_layout->addWidget(close_btn);

  dialog->setMinimumSize(1000, 800);
  dialog->exec();
  delete dialog;
}

void VehiclePanel::openLateralDelayDetails() {
  const UIState &s = *uiState();

  // Create dialog
  QDialog *dialog = new QDialog(this);
  dialog->setWindowTitle(tr("Lateral Actuator Delay"));
  dialog->setStyleSheet("QDialog { background-color: #292929; }");

  QVBoxLayout *main_layout = new QVBoxLayout(dialog);
  main_layout->setContentsMargins(50, 50, 50, 50);
  main_layout->setSpacing(30);

  // Title
  QLabel *title = new QLabel(tr("Lateral Actuator Delay Estimation"), dialog);
  title->setStyleSheet("QLabel { font-size: 48px; font-weight: bold; color: white; }");
  title->setAlignment(Qt::AlignCenter);
  main_layout->addWidget(title);

  // Status indicator
  QString status_text;
  QString status_color;
  if (s.scene.lateral_status == 1) {  // Estimated
    status_text = tr("ESTIMATED");
    status_color = "#5CB85C";  // Green
  } else if (s.scene.lateral_status == 2) {  // Invalid
    status_text = tr("INVALID");
    status_color = "#D9534F";  // Red
  } else {  // Unestimated
    status_text = tr("NOT ESTIMATED");
    status_color = "#999";  // Gray
  }

  QLabel *status_lbl = new QLabel(status_text, dialog);
  status_lbl->setStyleSheet(QString("QLabel { font-size: 36px; font-weight: bold; color: %1; }").arg(status_color));
  status_lbl->setAlignment(Qt::AlignCenter);
  main_layout->addWidget(status_lbl);

  // Estimated delay value
  QFrame *delay_frame = new QFrame(dialog);
  delay_frame->setStyleSheet("QFrame { background-color: #1E1E1E; border-radius: 15px; padding: 20px; }");
  QVBoxLayout *delay_layout = new QVBoxLayout(delay_frame);

  QLabel *delay_label = new QLabel(tr("Estimated Delay"), dialog);
  delay_label->setStyleSheet("QLabel { font-size: 28px; color: #999; }");
  delay_label->setAlignment(Qt::AlignCenter);
  delay_layout->addWidget(delay_label);

  QString delay_text = QString("%1 s").arg(s.scene.lateral_delay_estimate, 0, 'f', 2);
  QLabel *delay_value = new QLabel(delay_text, dialog);
  delay_value->setStyleSheet("QLabel { font-size: 56px; font-weight: bold; color: white; }");
  delay_value->setAlignment(Qt::AlignCenter);
  delay_layout->addWidget(delay_value);

  main_layout->addWidget(delay_frame);

  // Standard deviation
  QFrame *std_frame = new QFrame(dialog);
  std_frame->setStyleSheet("QFrame { background-color: #1E1E1E; border-radius: 15px; padding: 20px; }");
  QVBoxLayout *std_layout = new QVBoxLayout(std_frame);

  QLabel *std_label = new QLabel(tr("Standard Deviation"), dialog);
  std_label->setStyleSheet("QLabel { font-size: 28px; color: #999; }");
  std_label->setAlignment(Qt::AlignCenter);
  std_layout->addWidget(std_label);

  QString std_text = QString("± %1 s").arg(s.scene.lateral_delay_std, 0, 'f', 2);
  QLabel *std_value = new QLabel(std_text, dialog);
  std_value->setStyleSheet("QLabel { font-size: 42px; font-weight: bold; color: white; }");
  std_value->setAlignment(Qt::AlignCenter);
  std_layout->addWidget(std_value);

  main_layout->addWidget(std_frame);

  // Calibration progress
  QFrame *cal_frame = new QFrame(dialog);
  cal_frame->setStyleSheet("QFrame { background-color: #1E1E1E; border-radius: 15px; padding: 20px; }");
  QHBoxLayout *cal_layout = new QHBoxLayout(cal_frame);

  QLabel *cal_label = new QLabel(tr("Calibration:"), dialog);
  cal_label->setStyleSheet("QLabel { font-size: 32px; color: #999; }");
  cal_layout->addWidget(cal_label);

  QString cal_text = QString("%1% (%2/%3 blocks)")
                        .arg(s.scene.lateral_cal_perc)
                        .arg(s.scene.lateral_valid_blocks)
                        .arg(10);  // BLOCK_NUM_NEEDED from lagd.py
  QLabel *cal_value = new QLabel(cal_text, dialog);
  cal_value->setStyleSheet("QLabel { font-size: 32px; font-weight: bold; color: white; }");
  cal_layout->addWidget(cal_value);
  cal_layout->addStretch();

  main_layout->addWidget(cal_frame);

  // Explanation text
  QLabel *explanation = new QLabel(tr("Lateral actuator delay is the time between commanded steering and actual vehicle response"), dialog);
  explanation->setStyleSheet("QLabel { font-size: 24px; color: #999; }");
  explanation->setAlignment(Qt::AlignCenter);
  explanation->setWordWrap(true);
  main_layout->addWidget(explanation);

  // Close button
  QPushButton *close_btn = new QPushButton(tr("Close"), dialog);
  close_btn->setStyleSheet("QPushButton { font-size: 36px; padding: 20px; background-color: #5CB85C; color: white; border-radius: 10px; }");
  QObject::connect(close_btn, &QPushButton::clicked, dialog, &QDialog::accept);
  main_layout->addWidget(close_btn);

  dialog->setMinimumSize(1000, 800);
  dialog->exec();
  delete dialog;
}

void VehiclePanel::openCurveSpeedDetails() {
  const UIState &s = *uiState();

  // Create dialog
  QDialog *dialog = new QDialog(this);
  dialog->setWindowTitle(tr("Curve Speed Control"));
  dialog->setStyleSheet("QDialog { background-color: #292929; }");

  QVBoxLayout *main_layout = new QVBoxLayout(dialog);
  main_layout->setContentsMargins(50, 50, 50, 50);
  main_layout->setSpacing(30);

  // Title
  QLabel *title = new QLabel(tr("Learned Curve Speed Parameters"), dialog);
  title->setStyleSheet("QLabel { font-size: 48px; font-weight: bold; color: white; }");
  title->setAlignment(Qt::AlignCenter);
  main_layout->addWidget(title);

  // Status indicator
  QString status_text;
  QString status_color;
  if (s.scene.curve_speed_status == 2) {  // Learned
    status_text = tr("LEARNED");
    status_color = "#5CB85C";  // Green
  } else if (s.scene.curve_speed_status == 1) {  // Learning
    status_text = tr("LEARNING");
    status_color = "#DAB825";  // Yellow
  } else if (s.scene.curve_speed_status == 3) {  // Invalid
    status_text = tr("INVALID");
    status_color = "#D9534F";  // Red
  } else {  // Unlearned
    status_text = tr("NOT LEARNED");
    status_color = "#999";  // Gray
  }

  QLabel *status_lbl = new QLabel(status_text, dialog);
  status_lbl->setStyleSheet(QString("QLabel { font-size: 36px; font-weight: bold; color: %1; }").arg(status_color));
  status_lbl->setAlignment(Qt::AlignCenter);
  main_layout->addWidget(status_lbl);

  // Lookahead time parameter
  QFrame *lookahead_frame = new QFrame(dialog);
  lookahead_frame->setStyleSheet("QFrame { background-color: #1E1E1E; border-radius: 15px; padding: 20px; }");
  QVBoxLayout *lookahead_layout = new QVBoxLayout(lookahead_frame);

  QLabel *lookahead_label = new QLabel(tr("Lookahead Time"), dialog);
  lookahead_label->setStyleSheet("QLabel { font-size: 28px; color: #999; }");
  lookahead_label->setAlignment(Qt::AlignCenter);
  lookahead_layout->addWidget(lookahead_label);

  QString lookahead_text = QString("%1 s").arg(s.scene.curve_speed_lookahead_time, 0, 'f', 1);
  QLabel *lookahead_value = new QLabel(lookahead_text, dialog);
  lookahead_value->setStyleSheet("QLabel { font-size: 48px; font-weight: bold; color: white; }");
  lookahead_value->setAlignment(Qt::AlignCenter);
  lookahead_layout->addWidget(lookahead_value);

  main_layout->addWidget(lookahead_frame);

  // Lateral acceleration limit parameter
  QFrame *lat_accel_frame = new QFrame(dialog);
  lat_accel_frame->setStyleSheet("QFrame { background-color: #1E1E1E; border-radius: 15px; padding: 20px; }");
  QVBoxLayout *lat_accel_layout = new QVBoxLayout(lat_accel_frame);

  QLabel *lat_accel_label = new QLabel(tr("Lateral Accel Limit"), dialog);
  lat_accel_label->setStyleSheet("QLabel { font-size: 28px; color: #999; }");
  lat_accel_label->setAlignment(Qt::AlignCenter);
  lat_accel_layout->addWidget(lat_accel_label);

  QString lat_accel_text = QString("%1 m/s²").arg(s.scene.curve_speed_lat_accel_limit, 0, 'f', 2);
  QLabel *lat_accel_value = new QLabel(lat_accel_text, dialog);
  lat_accel_value->setStyleSheet("QLabel { font-size: 48px; font-weight: bold; color: white; }");
  lat_accel_value->setAlignment(Qt::AlignCenter);
  lat_accel_layout->addWidget(lat_accel_value);

  main_layout->addWidget(lat_accel_frame);

  // Speed margin parameter
  QFrame *margin_frame = new QFrame(dialog);
  margin_frame->setStyleSheet("QFrame { background-color: #1E1E1E; border-radius: 15px; padding: 20px; }");
  QVBoxLayout *margin_layout = new QVBoxLayout(margin_frame);

  QLabel *margin_label = new QLabel(tr("Speed Margin"), dialog);
  margin_label->setStyleSheet("QLabel { font-size: 28px; color: #999; }");
  margin_label->setAlignment(Qt::AlignCenter);
  margin_layout->addWidget(margin_label);

  QString margin_text = QString("%1%").arg(s.scene.curve_speed_speed_margin * 100.0, 0, 'f', 0);
  QLabel *margin_value = new QLabel(margin_text, dialog);
  margin_value->setStyleSheet("QLabel { font-size: 48px; font-weight: bold; color: white; }");
  margin_value->setAlignment(Qt::AlignCenter);
  margin_layout->addWidget(margin_value);

  main_layout->addWidget(margin_frame);

  // Minimum curvature threshold parameter
  QFrame *curvature_frame = new QFrame(dialog);
  curvature_frame->setStyleSheet("QFrame { background-color: #1E1E1E; border-radius: 15px; padding: 20px; }");
  QVBoxLayout *curvature_layout = new QVBoxLayout(curvature_frame);

  QLabel *curvature_label = new QLabel(tr("Min Curvature Threshold"), dialog);
  curvature_label->setStyleSheet("QLabel { font-size: 28px; color: #999; }");
  curvature_label->setAlignment(Qt::AlignCenter);
  curvature_layout->addWidget(curvature_label);

  QString curvature_text = QString("%1 rad/m").arg(s.scene.curve_speed_min_curvature, 0, 'f', 4);
  QLabel *curvature_value = new QLabel(curvature_text, dialog);
  curvature_value->setStyleSheet("QLabel { font-size: 48px; font-weight: bold; color: white; }");
  curvature_value->setAlignment(Qt::AlignCenter);
  curvature_layout->addWidget(curvature_value);

  main_layout->addWidget(curvature_frame);

  // Learning progress
  QFrame *progress_frame = new QFrame(dialog);
  progress_frame->setStyleSheet("QFrame { background-color: #1E1E1E; border-radius: 15px; padding: 20px; }");
  QHBoxLayout *progress_layout = new QHBoxLayout(progress_frame);

  QLabel *progress_label = new QLabel(tr("Learning Progress:"), dialog);
  progress_label->setStyleSheet("QLabel { font-size: 32px; color: #999; }");
  progress_layout->addWidget(progress_label);

  QString progress_text = QString("%1% (%2/50 segments)")
                            .arg(s.scene.curve_speed_progress)
                            .arg(s.scene.curve_speed_valid_segments);
  QLabel *progress_value = new QLabel(progress_text, dialog);
  progress_value->setStyleSheet("QLabel { font-size: 32px; font-weight: bold; color: white; }");
  progress_layout->addWidget(progress_value);
  progress_layout->addStretch();

  main_layout->addWidget(progress_frame);

  // Explanation text
  QLabel *explanation = new QLabel(tr("Curve speed parameters are learned from your manual driving through curves (≥10s segments)"), dialog);
  explanation->setStyleSheet("QLabel { font-size: 24px; color: #999; }");
  explanation->setAlignment(Qt::AlignCenter);
  explanation->setWordWrap(true);
  main_layout->addWidget(explanation);

  // Close button
  QPushButton *close_btn = new QPushButton(tr("Close"), dialog);
  close_btn->setStyleSheet("QPushButton { font-size: 36px; padding: 20px; background-color: #5CB85C; color: white; border-radius: 10px; }");
  QObject::connect(close_btn, &QPushButton::clicked, dialog, &QDialog::accept);
  main_layout->addWidget(close_btn);

  dialog->setMinimumSize(1000, 1000);
  dialog->exec();
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
      actions << tr("Select") << tr("Delete") << tr("Cancel");
    } else {
      // Can't select or delete active model
      actions << tr("Delete") << tr("Cancel");
    }

    QString action = MultiOptionDialog::getSelection(
      tr("What would you like to do with") + "\n" + selection + "?",
      actions, "", this);

    if (action == tr("Select")) {
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
      actions << tr("Select") << tr("Delete") << tr("Cancel");
    } else {
      // Can't select or delete active model
      actions << tr("Delete") << tr("Cancel");
    }

    QString action = MultiOptionDialog::getSelection(
      tr("What would you like to do with") + "\n" + selection + "?",
      actions, "", this);

    if (action == tr("Select")) {
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

void VehiclePanel::updateState(const UIState &s) {
  // Update vehicle info title
  if (strlen(s.scene.bmw_car_fingerprint) > 0) {
    QString fingerprint = QString::fromUtf8(s.scene.bmw_car_fingerprint);
    vehicle_info_lbl->setText(QString("%1 Diagnostics").arg(fingerprint));
  } else {
    vehicle_info_lbl->setText("Vehicle Diagnostics");
  }

  // Always show personalized learning UI (not BMW-dependent)
  QString status_text;
  QString status_color;
  bool show_details_btn = false;

  switch(s.scene.personalized_status) {
    case 0:  // unlearned
      status_text = tr("Not learning");
      status_color = "white";
      break;
    case 1:  // learning
      status_text = QString("Learning: %1%").arg(s.scene.personalized_progress);
      status_color = "#DAB825";  // Yellow
      break;
    case 2:  // learned
      status_text = QString("Learned (%1%)").arg(s.scene.personalized_progress);
      status_color = "#5CB85C";  // Green
      show_details_btn = true;
      break;
    case 3:  // invalid
      status_text = tr("Invalid data");
      status_color = "#E22C2C";  // Red
      break;
    default:
      status_text = tr("Unknown");
      status_color = "white";
  }

  personalized_learning_lbl->setText(status_text);
  personalized_learning_lbl->setStyleSheet(QString("QLabel { color: %1; font-weight: bold; font-size: 36px; }").arg(status_color));
  personalized_learning_lbl->setVisible(true);  // Always visible
  personalized_details_btn->setVisible(show_details_btn);

  // Longitudinal actuator delay display (always visible)
  QString delay_status_text;
  QString delay_status_color;
  bool show_delay_details_btn = false;

  switch(s.scene.longitudinal_status) {
    case 0:  // unestimated / learning
      delay_status_text = tr("Learning");
      delay_status_color = "#999";  // Grey
      break;
    case 1: {  // estimated
      delay_status_text = QString("%1 s").arg(s.scene.longitudinal_delay_estimate, 0, 'f', 2);
      // Check if learned delay is being used by controlsd (delay == estimate means activated)
      float delay_diff = std::abs(s.scene.longitudinal_delay - s.scene.longitudinal_delay_estimate);
      if (delay_diff < 0.05) {  // Activated (50ms tolerance)
        delay_status_color = "#5CB85C";  // Green - learned and activated
      } else {
        delay_status_color = "#DAB825";  // Yellow - learned but not activated
      }
      show_delay_details_btn = true;
      break;
    }
    case 2:  // invalid
      delay_status_text = tr("Invalid data");
      delay_status_color = "#E22C2C";  // Red
      break;
    default:
      delay_status_text = tr("Unknown");
      delay_status_color = "#999";  // Grey
  }

  longitudinal_delay_lbl->setText(delay_status_text);
  longitudinal_delay_lbl->setStyleSheet(QString("QLabel { color: %1; font-weight: bold; font-size: 36px; }").arg(delay_status_color));
  longitudinal_delay_lbl->setVisible(true);  // Always visible
  longitudinal_delay_details_btn->setVisible(show_delay_details_btn);

  // Lateral actuator delay display (always visible)
  QString lateral_delay_status_text;
  QString lateral_delay_status_color;
  bool show_lateral_delay_details_btn = false;

  switch(s.scene.lateral_status) {
    case 0:  // unestimated / learning
      lateral_delay_status_text = tr("Learning");
      lateral_delay_status_color = "#999";  // Grey
      break;
    case 1: {  // estimated
      lateral_delay_status_text = QString("%1 s").arg(s.scene.lateral_delay_estimate, 0, 'f', 2);
      // Check if learned delay is being used by controlsd (delay == estimate means activated)
      float lateral_delay_diff = std::abs(s.scene.lateral_delay - s.scene.lateral_delay_estimate);
      if (lateral_delay_diff < 0.05) {  // Activated (50ms tolerance)
        lateral_delay_status_color = "#5CB85C";  // Green - learned and activated
      } else {
        lateral_delay_status_color = "#DAB825";  // Yellow - learned but not activated
      }
      show_lateral_delay_details_btn = true;
      break;
    }
    case 2:  // invalid
      lateral_delay_status_text = tr("Invalid data");
      lateral_delay_status_color = "#E22C2C";  // Red
      break;
    default:
      lateral_delay_status_text = tr("Unknown");
      lateral_delay_status_color = "#999";  // Grey
  }

  lateral_delay_lbl->setText(lateral_delay_status_text);
  lateral_delay_lbl->setStyleSheet(QString("QLabel { color: %1; font-weight: bold; font-size: 36px; }").arg(lateral_delay_status_color));
  lateral_delay_lbl->setVisible(true);  // Always visible
  lateral_delay_details_btn->setVisible(show_lateral_delay_details_btn);

  // Curve speed control learning display (always visible)
  QString curve_status_text;
  QString curve_status_color;
  bool show_curve_details_btn = false;

  switch(s.scene.curve_speed_status) {
    case 0:  // unlearned
      curve_status_text = tr("Not learning");
      curve_status_color = "#999";  // Grey
      break;
    case 1:  // learning
      curve_status_text = QString("Learning: %1%").arg(s.scene.curve_speed_progress);
      curve_status_color = "#DAB825";  // Yellow
      show_curve_details_btn = true;
      break;
    case 2:  // learned
      curve_status_text = QString("Learned (%1 segments)").arg(s.scene.curve_speed_valid_segments);
      curve_status_color = "#5CB85C";  // Green
      show_curve_details_btn = true;
      break;
    case 3:  // invalid
      curve_status_text = tr("Invalid data");
      curve_status_color = "#E22C2C";  // Red
      break;
    default:
      curve_status_text = tr("Unknown");
      curve_status_color = "#999";  // Grey
  }

  curve_speed_lbl->setText(curve_status_text);
  curve_speed_lbl->setStyleSheet(QString("QLabel { color: %1; font-weight: bold; font-size: 36px; }").arg(curve_status_color));
  curve_speed_lbl->setVisible(true);  // Always visible
  curve_speed_details_btn->setVisible(show_curve_details_btn);

  // BMW vitals display (always visible)
  // Show actual values when BMW detected, 0 values for offline development
  int coolant_temp = s.scene.bmw_diagnostics_available ? (int)s.scene.bmw_coolant_temp : 0;
  int oil_temp = s.scene.bmw_diagnostics_available ? (int)s.scene.bmw_oil_temp : 0;
  float battery_voltage = s.scene.bmw_diagnostics_available ? s.scene.bmw_battery_voltage : 0.0;

  // Coolant color coding
  QString coolant_color;
  if (coolant_temp > 95) {
    coolant_color = "#E22C2C";  // Red for high temp
  } else if (coolant_temp > 85) {
    coolant_color = "#DAB825";  // Yellow for warm
  } else {
    coolant_color = "#5CB85C";  // Green for normal
  }

  // Oil color coding
  QString oil_color;
  if (oil_temp > 125) {
    oil_color = "#E22C2C";  // Red for high temp
  } else if (oil_temp > 110) {
    oil_color = "#DAB825";  // Yellow for warm
  } else {
    oil_color = "#5CB85C";  // Green for normal
  }

  // Battery voltage color coding
  QString battery_color;
  if (battery_voltage < 11.5) {
    battery_color = "#E22C2C";  // Red for low voltage
  } else if (battery_voltage < 12.0) {
    battery_color = "#DAB825";  // Yellow for marginal
  } else {
    battery_color = "#5CB85C";  // Green for normal
  }

  // Update individual labels with colors
  coolant_lbl->setText(QString("Coolant: %1°C").arg(coolant_temp));
  coolant_lbl->setStyleSheet(QString("font-size: 40px; color: %1;").arg(coolant_color));

  oil_lbl->setText(QString("Oil: %1°C").arg(oil_temp));
  oil_lbl->setStyleSheet(QString("font-size: 40px; color: %1;").arg(oil_color));

  battery_lbl->setText(QString("Battery: %1V").arg(battery_voltage, 0, 'f', 1));
  battery_lbl->setStyleSheet(QString("font-size: 40px; color: %1;").arg(battery_color));

  bmw_vitals_widget->setVisible(true);  // Always visible
}

void VehiclePanel::updateVehicleInfo() {
  // Update vehicle information display - will be updated with actual fingerprint in updateState()
  vehicle_info_lbl->setText(tr("Vehicle Diagnostics"));

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

void VehiclePanel::checkForModelUpdates() {
  // Check if new models are available for download
  const QString script_path = "/data/openpilot/selfdrive/modeld/download_openpilot_models.py";

  QProcess process;
  process.start("python3", QStringList() << script_path << "check-updates");
  if (process.waitForFinished(5000)) {
    QString output = process.readAllStandardOutput();

    // Parse JSON output
    QJsonDocument doc = QJsonDocument::fromJson(output.toUtf8());
    if (!doc.isNull() && doc.isObject()) {
      QJsonObject obj = doc.object();
      int total = obj["total"].toInt();

      // Show download button only if new models are available
      download_models_btn->setVisible(total > 0);

      if (total > 0) {
        download_models_btn->setValue(QString("%1 new").arg(total));
      }
    }
  }
}

void VehiclePanel::checkAndDownloadModels() {
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
  QStringList model_display_list;
  QMap<QString, QJsonObject> model_map;  // Map display name to model data

  for (const QJsonValue &val : driving_models) {
    QJsonObject model_obj = val.toObject();
    QString name = model_obj["name"].toString();
    QString date = model_obj["date"].toString();
    QString display = QString("🚗 %1 (%2)").arg(name).arg(date);
    model_display_list << display;
    model_map[display] = model_obj;
  }
  for (const QJsonValue &val : dm_models) {
    QJsonObject model_obj = val.toObject();
    QString name = model_obj["name"].toString();
    QString date = model_obj["date"].toString();
    QString display = QString("👁️ %1 (%2)").arg(name).arg(date);
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

  // Success - update vehicle info and refresh
  updateVehicleInfo();
  checkForModelUpdates();

  // Return to panel automatically (no success alert)
}
