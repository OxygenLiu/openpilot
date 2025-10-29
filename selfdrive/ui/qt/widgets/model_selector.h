#pragma once

#include <QDialog>
#include <QLabel>
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QPushButton>
#include <QListWidget>
#include <QProcess>
#include <QTimer>
#include <QTextEdit>

#include "selfdrive/ui/ui.h"

enum class ModelType {
  DRIVING,  // Driving models (vision + policy)
  DM        // Driver Monitoring models
};

struct ModelInfo {
  QString id;
  QString name;
  QString version;
  QString description;
  bool has_onnx;
  int cached_pkl_count;
  int total_pkl_count;
  bool is_active;
};

class ModelSelectorDialog : public QDialog {
  Q_OBJECT

public:
  explicit ModelSelectorDialog(ModelType type, QWidget *parent = 0);
  ~ModelSelectorDialog();

private slots:
  void refreshModels();
  void onModelSelected();
  void swapModel();
  void onSwapFinished(int exitCode, QProcess::ExitStatus exitStatus);

private:
  void setupUI();
  void loadModels();
  void updateModelList();
  QString getActiveModel();
  QString getModelTypeArg();

  // UI Elements
  QLabel *active_model_label;
  QListWidget *model_list;
  QTextEdit *model_details;
  QPushButton *swap_btn;
  QPushButton *refresh_btn;
  QPushButton *close_btn;
  QLabel *status_label;

  // Data
  ModelType model_type;
  QString type_name;
  QList<ModelInfo> models;
  QString active_model_id;
  QString selected_model_id;
  QProcess *swap_process;
};
