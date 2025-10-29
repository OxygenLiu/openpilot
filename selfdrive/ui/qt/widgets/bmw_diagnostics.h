#pragma once

#include <QDialog>
#include <QLabel>
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QPushButton>
#include <QTextEdit>
#include <QTimer>

#include "selfdrive/ui/ui.h"

class BmwDiagnosticsDialog : public QDialog {
  Q_OBJECT

public:
  explicit BmwDiagnosticsDialog(QWidget *parent = 0);
  void updateData(const UIState &s);

private slots:
  void refreshData();
  void clearDtcs();

private:
  void setupUI();

  // UI Elements
  QLabel *dtc_count_label;
  QTextEdit *active_dtcs_text;
  QLabel *clear_status_label;
  QPushButton *refresh_btn;
  QPushButton *clear_dtcs_btn;
  QPushButton *close_btn;

  // Data
  const UIState *ui_state;
  QTimer *refresh_timer;
};