#include "selfdrive/ui/qt/offroad/developer_panel.h"
#include "selfdrive/ui/qt/widgets/ssh_keys.h"
#include "selfdrive/ui/qt/widgets/controls.h"

DeveloperPanel::DeveloperPanel(SettingsWindow *parent) : ListWidget(parent) {
  adbToggle = new ParamControl("AdbEnabled", tr("Enable ADB"),
            tr("ADB (Android Debug Bridge) allows connecting to your device over USB or over the network. See https://docs.comma.ai/how-to/connect-to-comma for more info."), "");
  addItem(adbToggle);

  // SSH keys
  addItem(new SshToggle());
  addItem(new SshControl());

  joystickToggle = new ParamControl("JoystickDebugMode", tr("Joystick Debug Mode"), "", "");
  QObject::connect(joystickToggle, &ParamControl::toggleFlipped, [=](bool state) {
    params.putBool("LongitudinalManeuverMode", false);
    longManeuverToggle->refresh();
  });
  addItem(joystickToggle);

  longManeuverToggle = new ParamControl("LongitudinalManeuverMode", tr("Longitudinal Maneuver Mode"), "", "");
  QObject::connect(longManeuverToggle, &ParamControl::toggleFlipped, [=](bool state) {
    params.putBool("JoystickDebugMode", false);
    joystickToggle->refresh();
  });
  addItem(longManeuverToggle);

  dccCalibrationToggle = new ParamControl("DccCalibrationMode",
    tr("DCC Calibration Mode"),
    tr("Disable openpilot engagement to allow manual DCC testing. All CAN data (velocity, DCC commands, acceleration) will continue to be logged for tuning analysis."),
    "");
  addItem(dccCalibrationToggle);

  experimentalLongitudinalToggle = new ParamControl(
    "AlphaLongitudinalEnabled",
    tr("openpilot Longitudinal Control (Alpha)"),
    QString("<b>%1</b><br><br>%2")
      .arg(tr("WARNING: openpilot longitudinal control is in alpha for this car and will disable Automatic Emergency Braking (AEB)."))
      .arg(tr("On this car, openpilot defaults to the car's built-in ACC instead of openpilot's longitudinal control. "
              "Enable this to switch to openpilot longitudinal control. Enabling Experimental mode is recommended when enabling openpilot longitudinal control alpha.")),
    ""
  );
  experimentalLongitudinalToggle->setConfirmation(true, false);
  QObject::connect(experimentalLongitudinalToggle, &ParamControl::toggleFlipped, [=]() {
    updateToggles(offroad);
  });
  addItem(experimentalLongitudinalToggle);

  // Lateral actuator delay display
  lateral_delay_lbl = new LabelControl(tr("Lateral Delay"), tr("Not estimated"));
  addItem(lateral_delay_lbl);

  // Details button for lateral delay (only shown when data is estimated)
  lateral_delay_details_btn = new ButtonControl(tr("View Delay Details"), tr("View lateral actuator delay estimation"));
  QObject::connect(lateral_delay_details_btn, &ButtonControl::clicked, this, &DeveloperPanel::openLateralDelayDetails);
  addItem(lateral_delay_details_btn);
  lateral_delay_details_btn->setVisible(false);  // Initially hidden

  // Joystick and longitudinal maneuvers should be hidden on release branches
  is_release = params.getBool("IsReleaseBranch");

  // Toggles should be not available to change in onroad state
  QObject::connect(uiState(), &UIState::offroadTransition, this, &DeveloperPanel::updateToggles);
  QObject::connect(uiState(), &UIState::uiUpdate, this, &DeveloperPanel::updateState);
}

void DeveloperPanel::updateToggles(bool _offroad) {
  for (auto btn : findChildren<ParamControl *>()) {
    btn->setVisible(!is_release);

    /*
     * experimentalLongitudinalToggle should be toggelable when:
     * - visible, and
     * - during onroad & offroad states
     */
    if (btn != experimentalLongitudinalToggle) {
      btn->setEnabled(_offroad);
    }
  }

  // longManeuverToggle and experimentalLongitudinalToggle should not be toggleable if the car does not have longitudinal control
  auto cp_bytes = params.get("CarParamsPersistent");
  if (!cp_bytes.empty()) {
    AlignedBuffer aligned_buf;
    capnp::FlatArrayMessageReader cmsg(aligned_buf.align(cp_bytes.data(), cp_bytes.size()));
    cereal::CarParams::Reader CP = cmsg.getRoot<cereal::CarParams>();

    if (!CP.getAlphaLongitudinalAvailable() || is_release) {
      params.remove("AlphaLongitudinalEnabled");
      experimentalLongitudinalToggle->setEnabled(false);
    }

    /*
     * experimentalLongitudinalToggle should be visible when:
     * - is not a release branch, and
     * - the car supports experimental longitudinal control (alpha)
     */
    experimentalLongitudinalToggle->setVisible(CP.getAlphaLongitudinalAvailable() && !is_release);

    longManeuverToggle->setEnabled(hasLongitudinalControl(CP) && _offroad);
    dccCalibrationToggle->setEnabled(hasLongitudinalControl(CP) && _offroad);
  } else {
    longManeuverToggle->setEnabled(false);
    experimentalLongitudinalToggle->setVisible(false);
  }
  experimentalLongitudinalToggle->refresh();

  offroad = _offroad;
}

void DeveloperPanel::showEvent(QShowEvent *event) {
  updateToggles(offroad);
}

void DeveloperPanel::openLateralDelayDetails() {
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

void DeveloperPanel::updateState(const UIState &s) {
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
}
