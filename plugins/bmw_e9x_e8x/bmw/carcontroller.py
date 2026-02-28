from opendbc.car import Bus, DT_CTRL
from opendbc.car.lateral import apply_dist_to_meas_limits
from bmw import bmwcan
from bmw.bmwcan import SteeringModes, CruiseStalk
from bmw.values import CarControllerParams, CanBus, BmwFlags, CruiseSettings
from opendbc.car.interfaces import CarControllerBase
from opendbc.can import CANPacker
from opendbc.car.common.conversions import Conversions as CV


# DO NOT CHANGE: Cruise control step size
CC_STEP = 1

# BMW Stock DCC CAN Frequencies
CRUISE_STALK_IDLE_TICK_STOCK = 0.2    # 5Hz
CRUISE_STALK_SINGLE_TICK_STOCK = 0.05 # 20Hz
CRUISE_STALK_HOLD_TICK_STOCK = 0.025  # 40Hz

# Openpilot DCC Emulation - Frequency-based command rates
CRUISE_STALK_PLUS1_SINGLE_TICK = 0.2    # 5Hz
CRUISE_STALK_PLUS1_HOLD_TICK = 0.025    # 40Hz
CRUISE_STALK_MINUS1_HOLD_TICK = 0.025   # 40Hz


class CarController(CarControllerBase):
  def __init__(self, dbc_name, CP):
    super().__init__(dbc_name, CP)
    self.flags = CP.flags
    self.min_cruise_speed = CP.minEnableSpeed
    self.min_cruise_setpoint = self.min_cruise_speed + CruiseSettings.MIN_SPEED_BUFFER * CV.KPH_TO_MS
    self.cruise_units = None

    self.cruise_cancel = False
    self.cruise_enabled_prev = False
    self.apply_torque_last = 0
    self.last_cruise_rx_timestamp = 0
    self.last_cruise_tx_timestamp = 0
    self.tx_cruise_stalk_counter_last = 0
    self.rx_cruise_stalk_counter_last = -1

    self.cruise_bus = CanBus.PT_CAN
    if CP.flags & BmwFlags.DYNAMIC_CRUISE_CONTROL:
      self.cruise_bus = CanBus.F_CAN

    self.packer = CANPacker(dbc_name[Bus.pt])

    self.dcc_ticks_remaining = 0
    self.dcc_last_tick_time = 0

    self.last_accel_time = 0

  def update(self, CC, CS, now_nanos):

    actuators = CC.actuators
    can_sends = []

    self.cruise_units = (CV.MS_TO_KPH if CS.is_metric else CV.MS_TO_MPH)

    v_target = actuators.speed if actuators.speed > 0 else CS.out.vEgoCluster

    v_current = CS.out.vEgo
    v_error = v_target - v_current

    accel = actuators.accel

    if CS.cruise_stalk_counter != self.rx_cruise_stalk_counter_last:
      self.tx_cruise_stalk_counter_last = CS.cruise_stalk_counter
      self.last_cruise_rx_timestamp = now_nanos
    self.rx_cruise_stalk_counter_last = CS.cruise_stalk_counter

    def cruise_cmd(cmd, tick_interval):
      time_since_cruise_sent = (now_nanos - self.last_cruise_tx_timestamp) / 1e9 + DT_CTRL / 10
      time_since_cruise_received = (now_nanos - self.last_cruise_rx_timestamp) / 1e9 + DT_CTRL / 10
      send = time_since_cruise_sent > tick_interval \
        and time_since_cruise_received > CRUISE_STALK_HOLD_TICK_STOCK/2 - DT_CTRL \
        and time_since_cruise_received < CRUISE_STALK_IDLE_TICK_STOCK/2 + DT_CTRL
      if send:
        tx_cruise_stalk_counter = self.tx_cruise_stalk_counter_last + 1
        if tx_cruise_stalk_counter == CS.cruise_stalk_counter + 1:
          tx_cruise_stalk_counter = tx_cruise_stalk_counter + 2
        tx_cruise_stalk_counter = tx_cruise_stalk_counter % 0xF
        can_sends.append(bmwcan.create_accel_command(self.packer, cmd, self.cruise_bus, tx_cruise_stalk_counter))
        self.tx_cruise_stalk_counter_last = tx_cruise_stalk_counter
        self.last_cruise_tx_timestamp = now_nanos

    if not CC.enabled and self.cruise_enabled_prev:
      self.cruise_cancel = True
    if (CS.out.cruiseState.speedCluster - self.min_cruise_speed) < 0.1 \
      and CS.out.vEgoCluster - self.min_cruise_speed < 0.4:
      self.cruise_cancel = True
    if not CS.out.cruiseState.enabled:
      self.cruise_cancel = False

    cruise_stalk_human_pressing = CS.cruise_stalk_resume or CS.cruise_stalk_cancel or CS.cruise_stalk_speed != 0

    if not cruise_stalk_human_pressing and CS.out.cruiseState.enabled:
      if self.cruise_cancel:
        cruise_cmd(CruiseStalk.cancel, CRUISE_STALK_SINGLE_TICK_STOCK)
        print("cancel")
      elif CC.enabled:
        if CS.out.gasPressed:
          cruise_cmd(CruiseStalk.plus1, CRUISE_STALK_PLUS1_SINGLE_TICK)
          self.dcc_ticks_remaining = 0
        else:
          current_time = now_nanos / 1e9

          if v_error > 0 and self.dcc_ticks_remaining > 0:
            self.dcc_ticks_remaining = 0

          v_error_setpoint = v_target - CS.out.cruiseState.speed

          v_ego_kph = CS.out.vEgo * 3.6
          if v_ego_kph <= 120.0:
            buffer_kph = (1.0 - v_ego_kph / 120.0) * 6.0
          else:
            buffer_kph = 0.0

          if v_error > 1.0/3.6 and accel > 0 and v_error_setpoint > -buffer_kph/3.6:
            v_error_kmh = v_error * 3.6
            setpoint_increase_needed = int(round(v_error_kmh))

            time_since_last_accel = current_time - self.last_accel_time

            if time_since_last_accel >= CRUISE_STALK_PLUS1_SINGLE_TICK and setpoint_increase_needed > 0:
              if setpoint_increase_needed >= 3:
                cruise_cmd(CruiseStalk.plus1, CRUISE_STALK_PLUS1_HOLD_TICK)
              else:
                cruise_cmd(CruiseStalk.plus1, CRUISE_STALK_PLUS1_SINGLE_TICK)
              self.last_accel_time = current_time
              self.dcc_ticks_remaining = 0

          elif v_error < -1.0/3.6 and accel < 0 and CS.out.cruiseState.speed > self.min_cruise_setpoint:
            if self.dcc_ticks_remaining == 0:
              v_error_ticks = int(round(-v_error * 3.6))
              max_ticks = max(0, int((CS.out.cruiseState.speed - self.min_cruise_setpoint) * 3.6))
              self.dcc_ticks_remaining = min(v_error_ticks, max_ticks)
              self.dcc_last_tick_time = current_time

            if self.dcc_ticks_remaining > 0:
              if current_time - self.dcc_last_tick_time >= CRUISE_STALK_MINUS1_HOLD_TICK:
                cruise_cmd(CruiseStalk.minus1, CRUISE_STALK_MINUS1_HOLD_TICK)
                self.dcc_ticks_remaining -= 1
                self.dcc_last_tick_time = current_time

          else:
            self.dcc_ticks_remaining = 0

    if self.flags & BmwFlags.STEPPER_SERVO_CAN:
      if CC.enabled and CC.latActive:
        new_steer = actuators.torque * CarControllerParams.STEER_MAX
        apply_torque = apply_dist_to_meas_limits(new_steer, self.apply_torque_last, CS.out.steeringTorqueEps,
                                           CarControllerParams.STEER_DELTA_UP, CarControllerParams.STEER_DELTA_DOWN,
                                           CarControllerParams.STEER_ERROR_MAX, CarControllerParams.STEER_MAX)
        can_sends.append(bmwcan.create_steer_command(self.frame, SteeringModes.TorqueControl, apply_torque))
      elif not CS.cruise_stalk_cancel and not CS.out.brakePressed and not CS.out.gasPressed and self.apply_torque_last != 0:
        can_sends.append(bmwcan.create_steer_command(self.frame, SteeringModes.SoftOff, self.apply_torque_last))
        apply_torque = CS.out.steeringTorqueEps
      else:
        apply_torque = 0
        can_sends.append(bmwcan.create_steer_command(self.frame, SteeringModes.Off))
      self.apply_torque_last = apply_torque

    if CC.enabled and (self.frame % 10) == 0:
      frame_number = self.frame
      print(f"Steering req: {actuators.torque}, Speed: {CS.out.vEgoCluster}, Frame number: {frame_number}")

    self.cruise_enabled_prev = CC.enabled

    new_actuators = actuators.as_builder()
    new_actuators.torque = self.apply_torque_last / CarControllerParams.STEER_MAX
    new_actuators.torqueOutputCan = self.apply_torque_last

    new_actuators.speed = v_target

    self.frame += 1
    return new_actuators, can_sends
