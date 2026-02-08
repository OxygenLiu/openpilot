#include "selfdrive/ui/qt/onroad/hud.h"

#include <cmath>

#include "selfdrive/ui/qt/util.h"

constexpr int SET_SPEED_NA = 255;

HudRenderer::HudRenderer() {}

void HudRenderer::updateState(const UIState &s) {
  is_metric = s.scene.is_metric;
  status = s.status;

  const SubMaster &sm = *(s.sm);
  if (sm.rcv_frame("carState") < s.scene.started_frame) {
    is_cruise_set = false;
    set_speed = SET_SPEED_NA;
    speed = 0.0;
    return;
  }

  const auto &controls_state = sm["controlsState"].getControlsState();
  const auto &car_state = sm["carState"].getCarState();
  const auto &longitudinal_plan = sm["longitudinalPlan"].getLongitudinalPlan();

  // Get curvature speed limiting status
  curvature_speed_limited = longitudinal_plan.getCurvatureSpeedLimited();

  // Handle older routes where vCruiseCluster is not set
  set_speed = car_state.getVCruiseCluster() == 0.0 ? controls_state.getVCruiseDEPRECATED() : car_state.getVCruiseCluster();
  is_cruise_set = set_speed > 0 && set_speed != SET_SPEED_NA;
  is_cruise_available = set_speed != -1;

  if (is_cruise_set && !is_metric) {
    set_speed *= KM_TO_MILE;
  }

  // Handle older routes where vEgoCluster is not set
  v_ego_cluster_seen = v_ego_cluster_seen || car_state.getVEgoCluster() != 0.0;
  float v_ego = v_ego_cluster_seen ? car_state.getVEgoCluster() : car_state.getVEgo();
  speed = std::max<float>(0.0f, v_ego * (is_metric ? MS_TO_KPH : MS_TO_MPH));

  // BMW vitals
  bmw_diagnostics_available = s.scene.bmw_diagnostics_available;
  bmw_coolant_temp = s.scene.bmw_coolant_temp;
  bmw_oil_temp = s.scene.bmw_oil_temp;
  bmw_battery_voltage = s.scene.bmw_battery_voltage;

  // Get RHD status for vitals positioning
  if (sm.updated("driverMonitoringState")) {
    auto dm_state = sm["driverMonitoringState"].getDriverMonitoringState();
    is_rhd = dm_state.getIsRHD();
  }
}

void HudRenderer::draw(QPainter &p, const QRect &surface_rect) {
  p.save();

  // Draw header gradient
  QLinearGradient bg(0, UI_HEADER_HEIGHT - (UI_HEADER_HEIGHT / 2.5), 0, UI_HEADER_HEIGHT);
  bg.setColorAt(0, QColor::fromRgbF(0, 0, 0, 0.45));
  bg.setColorAt(1, QColor::fromRgbF(0, 0, 0, 0));
  p.fillRect(0, 0, surface_rect.width(), UI_HEADER_HEIGHT, bg);


  if (is_cruise_available) {
    drawSetSpeed(p, surface_rect);
  }
  drawCurrentSpeed(p, surface_rect);

  // Draw BMW vitals in bottom right corner
  if (bmw_diagnostics_available) {
    drawBMWVitals(p, surface_rect);
  }

  p.restore();
}

void HudRenderer::drawSetSpeed(QPainter &p, const QRect &surface_rect) {
  // Draw outer box + border to contain set speed
  const QSize default_size = {172, 204};
  QSize set_speed_size = is_metric ? QSize(200, 204) : default_size;
  QRect set_speed_rect(QPoint(60 + (default_size.width() - set_speed_size.width()) / 2, 45), set_speed_size);

  // Draw set speed box
  p.setPen(QPen(QColor(255, 255, 255, 75), 6));
  p.setBrush(QColor(0, 0, 0, 166));
  p.drawRoundedRect(set_speed_rect, 32, 32);

  // Colors based on status
  QColor max_color = QColor(0xa6, 0xa6, 0xa6, 0xff);
  QColor set_speed_color = QColor(0x72, 0x72, 0x72, 0xff);
  if (is_cruise_set) {
    set_speed_color = QColor(255, 255, 255);
    if (status == STATUS_DISENGAGED) {
      max_color = QColor(255, 255, 255);
    } else if (status == STATUS_OVERRIDE) {
      max_color = QColor(0x91, 0x9b, 0x95, 0xff);
    } else {
      max_color = QColor(0x80, 0xd8, 0xa6, 0xff);
    }
  }

  // Draw "MAX" text
  p.setFont(InterFont(40, QFont::DemiBold));
  p.setPen(max_color);
  p.drawText(set_speed_rect.adjusted(0, 27, 0, 0), Qt::AlignTop | Qt::AlignHCenter, tr("MAX"));

  // Draw set speed
  QString setSpeedStr = is_cruise_set ? QString::number(std::nearbyint(set_speed)) : "–";
  p.setFont(InterFont(90, QFont::Bold));
  p.setPen(set_speed_color);
  p.drawText(set_speed_rect.adjusted(0, 77, 0, 0), Qt::AlignTop | Qt::AlignHCenter, setSpeedStr);
}

void HudRenderer::drawCurrentSpeed(QPainter &p, const QRect &surface_rect) {
  QString speedStr = QString::number(std::nearbyint(speed));

  // Set color based on curvature speed limiting status
  QColor speed_color = curvature_speed_limited ? QColor(255, 255, 0, 255) : QColor(255, 255, 255, 255);

  p.setFont(InterFont(176, QFont::Bold));
  QRect speed_rect = p.fontMetrics().boundingRect(speedStr);
  speed_rect.moveCenter({surface_rect.center().x(), 210 - speed_rect.height() / 2});
  p.setPen(speed_color);
  p.drawText(speed_rect.x(), speed_rect.bottom(), speedStr);

  p.setFont(InterFont(66));
  QString unit_str = is_metric ? tr("km/h") : tr("mph");
  drawText(p, surface_rect.center().x(), 290, unit_str, 200);
}

void HudRenderer::drawText(QPainter &p, int x, int y, const QString &text, int alpha) {
  QRect real_rect = p.fontMetrics().boundingRect(text);
  real_rect.moveCenter({x, y - real_rect.height() / 2});

  p.setPen(QColor(0xff, 0xff, 0xff, alpha));
  p.drawText(real_rect.x(), real_rect.bottom(), text);
}

void HudRenderer::drawBMWVitals(QPainter &p, const QRect &surface_rect) {
  // Draw BMW vitals at bottom corner, opposite to driver monitoring icon
  // For RHD: driver monitoring on right, vitals on left
  // For LHD: driver monitoring on left, vitals on right
  // Stacked vertically: Coolant (top), Oil (middle), Battery (bottom)

  const int btn_size = 192;
  int offset = UI_BORDER_SIZE + btn_size / 2;  // Same offset as driver monitoring (126px)
  int x = is_rhd ? offset : surface_rect.width() - offset;  // Opposite side from driver monitoring
  int y_base = surface_rect.height() - offset + 100;  // Move down 2 lines (50px per line)
  int line_spacing = 50;  // Vertical spacing between values
  const int alpha = 204;  // 80% opacity for less intrusive display

  p.setFont(InterFont(42, QFont::Bold));

  // Coolant temperature - color coded (top)
  int coolant_temp = (int)bmw_coolant_temp;
  QColor coolant_color;
  if (coolant_temp > 105) {
    coolant_color = QColor(0xE2, 0x2C, 0x2C);  // Red for extremely hot
  } else if (coolant_temp >= 90) {
    coolant_color = QColor(0xDA, 0xB8, 0x25);  // Yellow for warm
  } else {
    coolant_color = QColor(0x5C, 0xB8, 0x5C);  // Green for cool
  }
  p.setPen(coolant_color);
  drawText(p, x, y_base - line_spacing * 2, QString("%1°C").arg(coolant_temp), alpha);

  // Oil temperature - color coded (middle)
  int oil_temp = (int)bmw_oil_temp;
  QColor oil_color;
  if (oil_temp > 125) {
    oil_color = QColor(0xE2, 0x2C, 0x2C);  // Red for high temp
  } else if (oil_temp > 110) {
    oil_color = QColor(0xDA, 0xB8, 0x25);  // Yellow for warm
  } else {
    oil_color = QColor(0x5C, 0xB8, 0x5C);  // Green for normal
  }
  p.setPen(oil_color);
  drawText(p, x, y_base - line_spacing, QString("%1°C").arg(oil_temp), alpha);

  // Battery voltage - color coded (bottom)
  QColor battery_color;
  if (bmw_battery_voltage < 11.5) {
    battery_color = QColor(0xE2, 0x2C, 0x2C);  // Red for low voltage
  } else if (bmw_battery_voltage < 12.0) {
    battery_color = QColor(0xDA, 0xB8, 0x25);  // Yellow for marginal
  } else {
    battery_color = QColor(0x5C, 0xB8, 0x5C);  // Green for normal
  }
  p.setPen(battery_color);
  drawText(p, x, y_base, QString("%1V").arg(bmw_battery_voltage, 0, 'f', 1), alpha);
}
