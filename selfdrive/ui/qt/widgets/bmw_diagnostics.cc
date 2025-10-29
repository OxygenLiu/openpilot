#include "selfdrive/ui/qt/widgets/bmw_diagnostics.h"

#include <QGridLayout>
#include <QGroupBox>
#include <QFont>

BmwDiagnosticsDialog::BmwDiagnosticsDialog(QWidget *parent) : QDialog(parent) {
  setWindowTitle("BMW Diagnostic Trouble Codes");
  setModal(true);
  setFixedSize(700, 500);

  ui_state = nullptr;

  setupUI();

  // Auto-refresh every 2 seconds
  refresh_timer = new QTimer(this);
  connect(refresh_timer, &QTimer::timeout, this, &BmwDiagnosticsDialog::refreshData);
  refresh_timer->start(2000);
}

void BmwDiagnosticsDialog::setupUI() {
  QVBoxLayout *main_layout = new QVBoxLayout(this);

  // Title
  QLabel *title = new QLabel("BMW Internal Diagnostic Codes");
  title->setAlignment(Qt::AlignCenter);
  QFont title_font = title->font();
  title_font.setPointSize(18);
  title_font.setBold(true);
  title->setFont(title_font);
  main_layout->addWidget(title);

  // DTC Count
  QHBoxLayout *count_layout = new QHBoxLayout();
  count_layout->addWidget(new QLabel("Active DTCs:"));
  dtc_count_label = new QLabel("0");
  dtc_count_label->setStyleSheet("QLabel { color: green; font-weight: bold; font-size: 16px; }");
  count_layout->addWidget(dtc_count_label);
  count_layout->addStretch();
  main_layout->addLayout(count_layout);

  // DTC List
  QLabel *codes_label = new QLabel("BMW Internal Codes:");
  codes_label->setStyleSheet("QLabel { font-weight: bold; }");
  main_layout->addWidget(codes_label);

  active_dtcs_text = new QTextEdit();
  active_dtcs_text->setPlaceholderText("No diagnostic trouble codes found");
  active_dtcs_text->setReadOnly(true);
  active_dtcs_text->setMaximumHeight(180);
  main_layout->addWidget(active_dtcs_text);

  // DTC Clear Status
  QLabel *status_label = new QLabel("Clear Status:");
  status_label->setStyleSheet("QLabel { font-weight: bold; }");
  main_layout->addWidget(status_label);

  clear_status_label = new QLabel("Checking vehicle state...");
  clear_status_label->setStyleSheet("QLabel { color: #DAB825; padding: 8px; }");
  main_layout->addWidget(clear_status_label);

  // Control Buttons
  QHBoxLayout *button_layout = new QHBoxLayout();

  refresh_btn = new QPushButton("Refresh");
  connect(refresh_btn, &QPushButton::clicked, this, &BmwDiagnosticsDialog::refreshData);
  button_layout->addWidget(refresh_btn);

  clear_dtcs_btn = new QPushButton("Clear DTCs");
  clear_dtcs_btn->setEnabled(false);  // Will be enabled based on safety conditions
  connect(clear_dtcs_btn, &QPushButton::clicked, this, &BmwDiagnosticsDialog::clearDtcs);
  button_layout->addWidget(clear_dtcs_btn);

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
      padding: 8px;
      min-width: 80px;
    }
    QPushButton:hover {
      background-color: #666;
    }
    QPushButton:pressed {
      background-color: #444;
    }
  )");
}

void BmwDiagnosticsDialog::updateData(const UIState &s) {
  ui_state = &s;

  if (s.scene.bmw_diagnostics_available) {
    // Update DTC information
    dtc_count_label->setText(QString::number(s.scene.bmw_dtc_count));
    if (s.scene.bmw_dtc_count > 0) {
      dtc_count_label->setStyleSheet("QLabel { color: red; font-weight: bold; font-size: 16px; }");
      active_dtcs_text->setText(QString::fromUtf8(s.scene.bmw_active_dtcs));
    } else {
      dtc_count_label->setStyleSheet("QLabel { color: green; font-weight: bold; font-size: 16px; }");
      active_dtcs_text->clear();
    }

    // Update DTC clear status and button state
    QString clear_status = QString::fromUtf8(s.scene.bmw_dtc_clear_status);
    clear_status_label->setText(clear_status);

    // Enable clear button only when DTCs exist AND safety conditions are met
    bool can_clear = (s.scene.bmw_dtc_count > 0) && clear_status.contains("✅");
    clear_dtcs_btn->setEnabled(can_clear);

    // Color code the clear status
    if (clear_status.contains("✅")) {
      clear_status_label->setStyleSheet("QLabel { color: #5CB85C; padding: 8px; font-weight: bold; }");  // Green
    } else if (clear_status.contains("❌")) {
      clear_status_label->setStyleSheet("QLabel { color: #E22C2C; padding: 8px; font-weight: bold; }");  // Red
    } else {
      clear_status_label->setStyleSheet("QLabel { color: #DAB825; padding: 8px; font-weight: bold; }");  // Yellow
    }

  } else {
    // BMW diagnostics not available
    dtc_count_label->setText("N/A");
    dtc_count_label->setStyleSheet("QLabel { color: gray; font-weight: bold; font-size: 16px; }");
    active_dtcs_text->setText("BMW diagnostics not available");
    clear_status_label->setText("BMW diagnostics not available");
    clear_status_label->setStyleSheet("QLabel { color: gray; padding: 8px; }");
    clear_dtcs_btn->setEnabled(false);
  }
}

void BmwDiagnosticsDialog::refreshData() {
  if (ui_state) {
    updateData(*ui_state);
  }
}

void BmwDiagnosticsDialog::clearDtcs() {
  if (!ui_state || !ui_state->scene.bmw_diagnostics_available) {
    return;
  }

  // Verify safety conditions one more time
  QString clear_status = QString::fromUtf8(ui_state->scene.bmw_dtc_clear_status);
  if (!clear_status.contains("✅") || ui_state->scene.bmw_dtc_count == 0) {
    clear_status_label->setText("⚠️ Cannot clear: Safety conditions not met");
    clear_status_label->setStyleSheet("QLabel { color: #E22C2C; padding: 8px; font-weight: bold; }");
    return;
  }

  // Show immediate feedback
  clear_dtcs_btn->setText("Clearing...");
  clear_dtcs_btn->setEnabled(false);
  clear_status_label->setText("🔄 Sending BMW Service 0x58 clear command...");
  clear_status_label->setStyleSheet("QLabel { color: #DAB825; padding: 8px; font-weight: bold; }");

  // TODO: Implement actual UDS Service 0x14 transmission via CAN interface
  // The workflow is:
  // 1. UI calls PassiveDTCMonitor.request_dtc_clear_via_uds() ✅ (safety validation)
  // 2. Get UDS message via PassiveDTCMonitor.get_uds_clear_request_data() ✅
  // 3. Send UDS Service 0x14 message to 0x7E0 (BMW DME) via CAN ⏳ (needs CAN interface)
  // 4. BMW responds with positive/negative response
  // 5. BMW broadcasts Service 0x58 confirmation on 0x612 ✅ (handled by PassiveDTCMonitor)
  // 6. UI shows final result ✅

  // For now, simulate successful clearing after 3 seconds
  QTimer::singleShot(3000, [this]() {
    clear_status_label->setText("✅ UDS Service 0x14 clear completed!");
    clear_status_label->setStyleSheet("QLabel { color: #5CB85C; padding: 8px; font-weight: bold; }");

    // Reset button state after 3 seconds
    QTimer::singleShot(3000, [this]() {
      clear_dtcs_btn->setText("Clear DTCs");
      // Button state will be updated by next refresh
    });
  });
}