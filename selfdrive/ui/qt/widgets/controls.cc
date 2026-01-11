#include "selfdrive/ui/qt/widgets/controls.h"

#include <QHBoxLayout>
#include <QLabel>
#include <QPainter>
#include <QPushButton>
#include <QStyleOption>

AbstractControl::AbstractControl(const QString &title, const QString &desc, const QString &icon, QWidget *parent) : QFrame(parent) {
  QVBoxLayout *main_layout = new QVBoxLayout(this);
  main_layout->setMargin(0);

  hlayout = new QHBoxLayout;
  hlayout->setMargin(0);
  hlayout->setSpacing(20);

  // left icon
  icon_label = new QLabel(this);
  hlayout->addWidget(icon_label);
  if (!icon.isEmpty()) {
    icon_pixmap = QPixmap(icon).scaledToWidth(80, Qt::SmoothTransformation);
    icon_label->setPixmap(icon_pixmap);
    icon_label->setSizePolicy(QSizePolicy(QSizePolicy::Fixed, QSizePolicy::Fixed));
  }
  icon_label->setVisible(!icon.isEmpty());

  // title
  title_label = new QPushButton(title);
  title_label->setFixedHeight(120);
  title_label->setStyleSheet("font-size: 50px; font-weight: 400; text-align: left; border: none;");
  hlayout->addWidget(title_label, 1);

  // value next to control button
  value = new ElidedLabel();
  value->setAlignment(Qt::AlignRight | Qt::AlignVCenter);
  value->setStyleSheet("color: #aaaaaa");
  hlayout->addWidget(value);

  main_layout->addLayout(hlayout);

  // description
  description = new QLabel(desc);
  description->setContentsMargins(40, 20, 40, 20);
  description->setStyleSheet("font-size: 40px; color: grey");
  description->setWordWrap(true);
  description->setVisible(false);
  main_layout->addWidget(description);

  connect(title_label, &QPushButton::clicked, [=]() {
    if (!description->isVisible()) {
      emit showDescriptionEvent();
    }

    if (!description->text().isEmpty()) {
      description->setVisible(!description->isVisible());
    }
  });

  main_layout->addStretch();
}

void AbstractControl::hideEvent(QHideEvent *e) {
  if (description != nullptr) {
    description->hide();
  }
}

// controls

ButtonControl::ButtonControl(const QString &title, const QString &text, const QString &desc, QWidget *parent) : AbstractControl(title, desc, "", parent) {
  btn.setText(text);
  btn.setStyleSheet(R"(
    QPushButton {
      padding: 0;
      border-radius: 50px;
      font-size: 35px;
      font-weight: 500;
      color: #E4E4E4;
      background-color: #393939;
    }
    QPushButton:pressed {
      background-color: #4a4a4a;
    }
    QPushButton:disabled {
      color: #33E4E4E4;
    }
  )");
  btn.setFixedSize(250, 100);
  QObject::connect(&btn, &QPushButton::clicked, this, &ButtonControl::clicked);
  hlayout->addWidget(&btn);
}

LateralDelayEstimation::LateralDelayEstimation(QWidget *parent) : QFrame(parent) {
  QHBoxLayout *main_layout = new QHBoxLayout(this);
  main_layout->setMargin(0);
  main_layout->setSpacing(20);

  title_label = new QLabel(tr("Lateral Delay Estimation"), this);
  title_label->setFixedHeight(120);
  title_label->setStyleSheet("QLabel { font-size: 50px; font-weight: 400; }");
  title_label->setAlignment(Qt::AlignLeft | Qt::AlignVCenter);
  main_layout->addWidget(title_label, 1);

  value_label = new QLabel(tr("in progress"), this);
  value_label->setStyleSheet("QLabel { font-size: 45px; color: #C9C9C9; }");
  value_label->setAlignment(Qt::AlignRight | Qt::AlignVCenter);
  value_label->setWordWrap(true);
  main_layout->addWidget(value_label, 1);

  reset_btn = new QPushButton(tr("RESET"), this);
  reset_btn->setFixedSize(400, 100);
  reset_btn->setStyleSheet(R"(
    QPushButton {
      background-color: #465BEA;
      color: white;
      border-radius: 10px;
      font-size: 40px;
      font-weight: 500;
    }
    QPushButton:pressed {
      background-color: #3049F4;
    }
  )");
  reset_btn->setVisible(false);
  connect(reset_btn, &QPushButton::clicked, this, &LateralDelayEstimation::resetClicked);
  main_layout->addWidget(reset_btn);
}

void LateralDelayEstimation::updateStatus(int status, int cal_perc, int valid_blocks, float delay_estimate, float delay_std, float current_delay) {
  switch(status) {
    case 0:  // unestimated / learning
      if (cal_perc > 0) {
        value_label->setText(QString(tr("in progress (%1%, %2/%3 blocks)"))
          .arg(cal_perc)
          .arg(valid_blocks)
          .arg(10));
      } else {
        value_label->setText(tr("in progress"));
      }
      value_label->setStyleSheet("QLabel { font-size: 45px; color: #C9C9C9; }");
      reset_btn->setVisible(false);
      break;
    case 1: {  // estimated
      QString value_text = QString("%1 s").arg(delay_estimate, 0, 'f', 3);
      float lateral_delay_diff = std::abs(current_delay - delay_estimate);
      if (lateral_delay_diff < 0.05) {  // Activated
        value_label->setText(QString("<font color='#5CB85C'>%1</font>").arg(value_text));
      } else {
        QString std_text = QString("± %1 s").arg(delay_std, 0, 'f', 3);
        value_label->setText(QString("<font color='#DAB825'>%1 (std: %2)</font>").arg(value_text).arg(std_text));
      }
      value_label->setStyleSheet("QLabel { font-size: 45px; }");
      reset_btn->setVisible(true);
      break;
    }
    case 2:  // invalid
      value_label->setText(QString("<font color='#E22C2C'>Invalid</font>"));
      value_label->setStyleSheet("QLabel { font-size: 45px; }");
      reset_btn->setVisible(true);
      break;
    default:
      value_label->setText("");
      value_label->setStyleSheet("QLabel { font-size: 45px; color: #C9C9C9; }");
      reset_btn->setVisible(false);
  }
}

// ElidedLabel

ElidedLabel::ElidedLabel(QWidget *parent) : ElidedLabel({}, parent) {}

ElidedLabel::ElidedLabel(const QString &text, QWidget *parent) : QLabel(text.trimmed(), parent) {
  setSizePolicy(QSizePolicy::Preferred, QSizePolicy::Preferred);
  setMinimumWidth(1);
}

void ElidedLabel::resizeEvent(QResizeEvent* event) {
  QLabel::resizeEvent(event);
  lastText_ = elidedText_ = "";
}

void ElidedLabel::paintEvent(QPaintEvent *event) {
  const QString curText = text();
  if (curText != lastText_) {
    elidedText_ = fontMetrics().elidedText(curText, Qt::ElideRight, contentsRect().width());
    lastText_ = curText;
  }

  QPainter painter(this);
  drawFrame(&painter);
  QStyleOption opt;
  opt.initFrom(this);
  style()->drawItemText(&painter, contentsRect(), alignment(), opt.palette, isEnabled(), elidedText_, foregroundRole());
}

// ParamControl

ParamControl::ParamControl(const QString &param, const QString &title, const QString &desc, const QString &icon, QWidget *parent)
    : ToggleControl(title, desc, icon, false, parent) {
  key = param.toStdString();
  QObject::connect(this, &ParamControl::toggleFlipped, this, &ParamControl::toggleClicked);
}

void ParamControl::toggleClicked(bool state) {
  auto do_confirm = [this]() {
    QString content("<body><h2 style=\"text-align: center;\">" + title_label->text() + "</h2><br>"
                    "<p style=\"text-align: center; margin: 0 128px; font-size: 50px;\">" + getDescription() + "</p></body>");
    return ConfirmationDialog(content, tr("Enable"), tr("Cancel"), true, this).exec();
  };

  bool confirmed = store_confirm && params.getBool(key + "Confirmed");
  if (!confirm || confirmed || !state || do_confirm()) {
    if (store_confirm && state) params.putBool(key + "Confirmed", true);
    params.putBool(key, state);
    setIcon(state);
  } else {
    toggle.togglePosition();
  }
}
