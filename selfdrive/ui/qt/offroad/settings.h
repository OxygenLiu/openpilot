#pragma once

#include <map>
#include <string>

#include <QButtonGroup>
#include <QFrame>
#include <QLabel>
#include <QPushButton>
#include <QStackedWidget>
#include <QWidget>

#include "selfdrive/ui/ui.h"
#include "selfdrive/ui/qt/util.h"
#include "selfdrive/ui/qt/widgets/controls.h"

// ********** settings window + top-level panels **********
class SettingsWindow : public QFrame {
  Q_OBJECT

public:
  explicit SettingsWindow(QWidget *parent = 0);
  void setCurrentPanel(int index, const QString &param = "");

protected:
  void showEvent(QShowEvent *event) override;

signals:
  void closeSettings();
  void reviewTrainingGuide();
  void showDriverView();
  void expandToggleDescription(const QString &param);
  void scrollToToggle(const QString &param);
  void networkConnectivityChanged(bool connected);  // Forward connectivity for model updates

private:
  QPushButton *sidebar_alert_widget;
  QWidget *sidebar_widget;
  QButtonGroup *nav_btns;
  QStackedWidget *panel_widget;
};

class DevicePanel : public ListWidget {
  Q_OBJECT
public:
  explicit DevicePanel(SettingsWindow *parent);

signals:
  void reviewTrainingGuide();
  void showDriverView();

private slots:
  void poweroff();
  void reboot();
  void updateCalibDescription();

private:
  Params params;
  ButtonControl *pair_device;
  ButtonControl *resetCalibBtn;
};

class TogglesPanel : public ListWidget {
  Q_OBJECT
public:
  explicit TogglesPanel(SettingsWindow *parent);
  void showEvent(QShowEvent *event) override;

public slots:
  void expandToggleDescription(const QString &param);
  void scrollToToggle(const QString &param);

private slots:
  void updateState(const UIState &s);

private:
  Params params;
  std::map<std::string, ParamControl*> toggles;
  ButtonParamControl *long_personality_setting;

  void updateToggles();
};

class SoftwarePanel : public ListWidget {
  Q_OBJECT
public:
  explicit SoftwarePanel(QWidget* parent = nullptr);

private:
  void showEvent(QShowEvent *event) override;
  void updateLabels();
  void checkForUpdates();

  bool is_onroad = false;

  QLabel *onroadLbl;
  LabelControl *versionLbl;
  ButtonControl *installBtn;
  ButtonControl *downloadBtn;
  ButtonControl *targetBranchBtn;

  Params params;
  ParamWatcher *fs_watch;
};

class ModelsPanel : public ListWidget {
  Q_OBJECT
public:
  explicit ModelsPanel(SettingsWindow *parent);

private slots:
  void openDrivingModelSelector();
  void openDMModelSelector();
  void updateRegistryOrDownload();
  void checkUpdateStatus();  // Poll Params for async update status
  void onNetworkConnectivityChanged(bool connected);  // Update button state based on connectivity

private:
  Params params;
  ButtonControl *driving_model_selector_btn;
  ButtonControl *dm_model_selector_btn;
  ButtonControl *download_models_btn;
  bool models_ready_to_download = false;  // Track if new models are available
  bool network_connected = false;  // Track GitHub connectivity for UPDATE button
  QTimer *update_timer = nullptr;  // Timer to poll Params for update status
  int update_poll_count = 0;  // Track polling iterations for timeout (60 second max)
  void updateModelButtonText();
  void downloadNewModels();
};

// Forward declaration
class FirehosePanel;
