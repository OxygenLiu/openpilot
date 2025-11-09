#include "selfdrive/ui/qt/widgets/model_selector.h"

#include <QGridLayout>
#include <QGroupBox>
#include <QFont>
#include <QMessageBox>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>

ModelSelectorDialog::ModelSelectorDialog(ModelType type, QWidget *parent) : QDialog(parent), model_type(type) {
  // Set type-specific parameters
  if (model_type == ModelType::DRIVING) {
    type_name = "Driving Model";
    setWindowTitle("🚗 Driving Model Selector");
  } else {
    type_name = "Driver Monitoring Model";
    setWindowTitle("👁️ Driver Monitoring Model Selector");
  }

  setModal(true);
  setFixedSize(900, 650);

  swap_process = nullptr;

  setupUI();
  loadModels();
}

ModelSelectorDialog::~ModelSelectorDialog() {
  if (swap_process) {
    swap_process->kill();
    swap_process->waitForFinished();
    delete swap_process;
  }
}

void ModelSelectorDialog::setupUI() {
  QVBoxLayout *main_layout = new QVBoxLayout(this);

  // Title
  QLabel *title = new QLabel("🚗 Driving Model Selector");
  title->setAlignment(Qt::AlignCenter);
  QFont title_font = title->font();
  title_font.setPointSize(20);
  title_font.setBold(true);
  title->setFont(title_font);
  main_layout->addWidget(title);

  // Active Model Display
  QHBoxLayout *active_layout = new QHBoxLayout();
  active_layout->addWidget(new QLabel("Active Model:"));
  active_model_label = new QLabel("Loading...");
  active_model_label->setStyleSheet("QLabel { color: #4A90E2; font-weight: bold; font-size: 16px; }");
  active_layout->addWidget(active_model_label);
  active_layout->addStretch();
  main_layout->addLayout(active_layout);

  // Model List
  QLabel *list_label = new QLabel("Available Models:");
  list_label->setStyleSheet("QLabel { font-weight: bold; margin-top: 10px; }");
  main_layout->addWidget(list_label);

  model_list = new QListWidget();
  model_list->setMaximumHeight(200);
  connect(model_list, &QListWidget::itemSelectionChanged, this, &ModelSelectorDialog::onModelSelected);
  main_layout->addWidget(model_list);

  // Model Details
  QLabel *details_label = new QLabel("Model Details:");
  details_label->setStyleSheet("QLabel { font-weight: bold; margin-top: 10px; }");
  main_layout->addWidget(details_label);

  model_details = new QTextEdit();
  model_details->setReadOnly(true);
  model_details->setMaximumHeight(150);
  model_details->setPlaceholderText("Select a model to see details");
  main_layout->addWidget(model_details);

  // Status Label
  status_label = new QLabel("");
  status_label->setStyleSheet("QLabel { color: #DAB825; padding: 5px; }");
  status_label->setWordWrap(true);
  main_layout->addWidget(status_label);

  // Control Buttons
  QHBoxLayout *button_layout = new QHBoxLayout();

  refresh_btn = new QPushButton("Refresh");
  connect(refresh_btn, &QPushButton::clicked, this, &ModelSelectorDialog::refreshModels);
  button_layout->addWidget(refresh_btn);

  swap_btn = new QPushButton("Swap Model");
  swap_btn->setEnabled(false);
  connect(swap_btn, &QPushButton::clicked, this, &ModelSelectorDialog::swapModel);
  button_layout->addWidget(swap_btn);

  button_layout->addStretch();

  close_btn = new QPushButton("Close");
  connect(close_btn, &QPushButton::clicked, this, &QDialog::accept);
  button_layout->addWidget(close_btn);

  main_layout->addLayout(button_layout);

  // Set dark theme styling
  setStyleSheet(R"(
    QDialog {
      background-color: #393939;
      color: white;
    }
    QListWidget {
      background-color: #555;
      border: 1px solid #777;
      border-radius: 5px;
      padding: 5px;
      font-size: 14px;
    }
    QListWidget::item {
      padding: 8px;
      border-radius: 3px;
    }
    QListWidget::item:selected {
      background-color: #4A90E2;
    }
    QTextEdit {
      background-color: #555;
      border: 1px solid #777;
      border-radius: 5px;
      padding: 10px;
      font-family: monospace;
      font-size: 12px;
    }
    QPushButton {
      background-color: #555;
      border: 1px solid #777;
      border-radius: 5px;
      padding: 10px 20px;
      font-size: 14px;
      min-width: 100px;
    }
    QPushButton:hover {
      background-color: #666;
    }
    QPushButton:pressed {
      background-color: #444;
    }
    QPushButton:disabled {
      background-color: #333;
      color: #777;
    }
  )");
}

QString ModelSelectorDialog::getModelTypeArg() {
  return (model_type == ModelType::DRIVING) ? "driving" : "dm";
}

QString ModelSelectorDialog::getActiveModel() {
  // Call model_swapper.py to get active model
  QProcess process;
  process.start("python3", QStringList()
    << "/data/openpilot/selfdrive/modeld/model_swapper.py"
    << "--type" << getModelTypeArg()
    << "active");
  process.waitForFinished(3000);

  QString output = process.readAllStandardOutput().trimmed();

  // Parse output: "Active <type> Model: model_id"
  if (output.contains(":")) {
    return output.split(":").last().trimmed();
  }

  return "unknown";
}

void ModelSelectorDialog::loadModels() {
  models.clear();

  // Get active model
  active_model_id = getActiveModel();
  active_model_label->setText(active_model_id);

  // Call model_swapper.py list command
  QProcess process;
  process.start("python3", QStringList()
    << "/data/openpilot/selfdrive/modeld/model_swapper.py"
    << "--type" << getModelTypeArg()
    << "list");
  process.waitForFinished(5000);

  QString output = process.readAllStandardOutput();

  // Parse output (line-based format)
  QStringList lines = output.split('\n', QString::SkipEmptyParts);

  ModelInfo current_model;
  bool in_model = false;

  for (const QString &line : lines) {
    QString trimmed = line.trimmed();

    if (trimmed.isEmpty() || trimmed.startsWith("Available models")) {
      continue;
    }

    // Model ID line (not indented)
    if (!trimmed.startsWith("Name:") && !trimmed.startsWith("Version:") &&
        !trimmed.startsWith("Status:") && !trimmed.startsWith("Description:")) {
      // Save previous model if we were parsing one
      if (in_model) {
        current_model.is_active = (current_model.id == active_model_id);
        models.append(current_model);
      }

      // Start new model
      current_model = ModelInfo();
      current_model.id = trimmed;
      current_model.has_onnx = false;
      current_model.cached_pkl_count = 0;
      current_model.total_pkl_count = (model_type == ModelType::DRIVING) ? 4 : 1;
      in_model = true;
    } else if (trimmed.startsWith("Name:")) {
      current_model.name = trimmed.mid(5).trimmed();
    } else if (trimmed.startsWith("Version:")) {
      current_model.version = trimmed.mid(8).trimmed();
    } else if (trimmed.startsWith("Status:")) {
      QString status = trimmed.mid(7).trimmed();
      if (status.contains("ONNX")) {
        current_model.has_onnx = true;
      }
      // Parse cached PKL count: "✓ ONNX (3/5 PKL cached)"
      if (status.contains("PKL cached")) {
        int start = status.indexOf('(');
        int end = status.indexOf('/');
        if (start > 0 && end > start) {
          QString count_str = status.mid(start + 1, end - start - 1).trimmed();
          current_model.cached_pkl_count = count_str.toInt();
        }
      }
    }
  }

  // Add last model
  if (in_model) {
    current_model.is_active = (current_model.id == active_model_id);
    models.append(current_model);
  }

  updateModelList();
}

void ModelSelectorDialog::updateModelList() {
  model_list->clear();

  if (models.isEmpty()) {
    QListWidgetItem *item = new QListWidgetItem("No models available");
    item->setFlags(Qt::NoItemFlags);  // Disable selection
    model_list->addItem(item);
    status_label->setText("No models found. Download models using download_openpilot_models.py");
    return;
  }

  for (const ModelInfo &model : models) {
    QString item_text;

    if (model.is_active) {
      item_text = QString("✓ %1 (Active)").arg(model.name.isEmpty() ? model.id : model.name);
    } else {
      item_text = model.name.isEmpty() ? model.id : model.name;
    }

    // Add cache indicator
    if (model.cached_pkl_count > 0) {
      item_text += QString(" [%1/5 PKL]").arg(model.cached_pkl_count);
    }

    QListWidgetItem *item = new QListWidgetItem(item_text);
    item->setData(Qt::UserRole, model.id);

    if (model.is_active) {
      QFont font = item->font();
      font.setBold(true);
      item->setFont(font);
    }

    if (!model.has_onnx) {
      item->setForeground(Qt::gray);
    }

    model_list->addItem(item);
  }

  status_label->setText(QString("Found %1 models. Select one to swap.").arg(models.size()));
}

void ModelSelectorDialog::onModelSelected() {
  QListWidgetItem *item = model_list->currentItem();
  if (!item) {
    swap_btn->setEnabled(false);
    model_details->clear();
    return;
  }

  selected_model_id = item->data(Qt::UserRole).toString();

  // Find model info
  ModelInfo *selected = nullptr;
  for (ModelInfo &m : models) {
    if (m.id == selected_model_id) {
      selected = &m;
      break;
    }
  }

  if (!selected) {
    swap_btn->setEnabled(false);
    model_details->clear();
    return;
  }

  // Display model details
  QString details;
  details += QString("ID: %1\n").arg(selected->id);
  details += QString("Name: %1\n").arg(selected->name.isEmpty() ? "Unknown" : selected->name);
  details += QString("Version: %1\n").arg(selected->version.isEmpty() ? "Unknown" : selected->version);
  details += QString("\n");
  details += QString("ONNX Files: %1\n").arg(selected->has_onnx ? "✓ Available" : "✗ Missing");
  details += QString("Cached PKL: %1/5\n").arg(selected->cached_pkl_count);
  details += QString("\n");

  if (selected->cached_pkl_count == 5) {
    details += "⚡ Instant swap (PKL cached)\n";
  } else if (selected->has_onnx) {
    details += "⏳ First boot will compile ONNX→PKL (~2-5 min)\n";
  } else {
    details += "⚠️ Missing ONNX files - cannot use this model\n";
  }

  model_details->setText(details);

  // Enable swap button if not active and has ONNX
  swap_btn->setEnabled(!selected->is_active && selected->has_onnx);

  if (selected->is_active) {
    status_label->setText("This model is already active.");
    status_label->setStyleSheet("QLabel { color: #5CB85C; padding: 5px; }");
  } else if (!selected->has_onnx) {
    status_label->setText("Cannot swap: ONNX files missing. Download model first.");
    status_label->setStyleSheet("QLabel { color: #E22C2C; padding: 5px; }");
  } else {
    status_label->setText("Ready to swap. Openpilot will restart after swap.");
    status_label->setStyleSheet("QLabel { color: #DAB825; padding: 5px; }");
  }
}

void ModelSelectorDialog::refreshModels() {
  status_label->setText("Refreshing model list...");
  status_label->setStyleSheet("QLabel { color: #4A90E2; padding: 5px; }");
  loadModels();
}

void ModelSelectorDialog::swapModel() {
  if (selected_model_id.isEmpty()) {
    return;
  }

  // Confirm swap
  QMessageBox confirm_box(this);
  confirm_box.setWindowTitle("Confirm Model Swap");
  confirm_box.setText(QString("Swap to model: %1?").arg(selected_model_id));
  confirm_box.setInformativeText("Openpilot will restart. This may take a few minutes on first use.");
  confirm_box.setStandardButtons(QMessageBox::Yes | QMessageBox::No);
  confirm_box.setDefaultButton(QMessageBox::No);

  if (confirm_box.exec() != QMessageBox::Yes) {
    return;
  }

  // Disable controls during swap
  swap_btn->setEnabled(false);
  refresh_btn->setEnabled(false);
  model_list->setEnabled(false);
  status_label->setText("Swapping model... Please wait.");
  status_label->setStyleSheet("QLabel { color: #4A90E2; padding: 5px; }");

  // Call model_swapper.py swap command
  if (swap_process) {
    delete swap_process;
  }

  swap_process = new QProcess(this);
  connect(swap_process,
          QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished),
          this,
          &ModelSelectorDialog::onSwapFinished);

  swap_process->start("python3", QStringList()
    << "/data/openpilot/selfdrive/modeld/model_swapper.py"
    << "--type" << getModelTypeArg()
    << "swap"
    << selected_model_id);
}

void ModelSelectorDialog::onSwapFinished(int exitCode, QProcess::ExitStatus exitStatus) {
  // Re-enable controls
  swap_btn->setEnabled(true);
  refresh_btn->setEnabled(true);
  model_list->setEnabled(true);

  if (exitCode == 0 && exitStatus == QProcess::NormalExit) {
    status_label->setText("✓ Model swapped successfully! Restart openpilot for changes to take effect.");
    status_label->setStyleSheet("QLabel { color: #5CB85C; padding: 5px; font-weight: bold; }");

    // Show restart prompt
    QMessageBox restart_box(this);
    restart_box.setWindowTitle("Model Swapped");
    restart_box.setText("Model swapped successfully!");
    restart_box.setInformativeText("Restart openpilot now to use the new model?");
    restart_box.setStandardButtons(QMessageBox::Yes | QMessageBox::No);
    restart_box.setDefaultButton(QMessageBox::Yes);

    if (restart_box.exec() == QMessageBox::Yes) {
      // Trigger openpilot restart via Params
      Params().putBool("OnroadCycleRequested", true);
      accept();  // Close dialog
    } else {
      loadModels();  // Refresh to show new active model
    }
  } else {
    QString error_output = swap_process->readAllStandardError();
    status_label->setText(QString("✗ Swap failed: %1").arg(error_output.isEmpty() ? "Unknown error" : error_output));
    status_label->setStyleSheet("QLabel { color: #E22C2C; padding: 5px; }");
  }
}
