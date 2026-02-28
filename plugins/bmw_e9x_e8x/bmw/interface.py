#!/usr/bin/env python3
import numpy as np
from opendbc.car import structs
from opendbc.car.common.conversions import Conversions as CV
from opendbc.car import get_safety_config
from bmw.values import CanBus, BmwFlags, CarControllerParams
from opendbc.car.interfaces import CarInterfaceBase
from bmw.carcontroller import CarController
from bmw.carstate import CarState

TransmissionType = structs.CarParams.TransmissionType


def detect_stepper_override(steer_cmd, steer_act, v_ego, centering_coeff, steer_friction_torque):
  release_angle = steer_friction_torque / (max(v_ego, 1) ** 2 * centering_coeff)

  override = False
  margin_value = 1
  if abs(steer_cmd) > release_angle:
    if steer_cmd > 0:
      override |= steer_act - steer_cmd > margin_value
      override |= steer_act < 0
    else:
      override |= steer_act - steer_cmd < -margin_value
      override |= steer_act > 0
  return override


class CarInterface(CarInterfaceBase):
  CarState = CarState
  CarController = CarController

  def __init__(self, CP, *args, **kwargs):
    super().__init__(CP, *args, **kwargs)

  @staticmethod
  def get_steer_feedforward_servotronic(desired_angle, v_ego):
    angle_bp = [-40.0, -6.0, -4.0, -3.0, -2.0, -1.0, -0.5,  0.5,  1.0,  2.0,  3.0,  4.0,  6.0, 40.0]
    hold_torque_v  = [-6, -2.85, -2.5, -2.25, -2, -1.65, -1, 1, 1.65, 2, 2.25, 2.5, 2.85, 6]
    hold_torque = np.interp(desired_angle, angle_bp, hold_torque_v)
    return hold_torque

  @staticmethod
  def get_steer_feedforward(desired_angle, v_ego):
    angle_bp = [-40.0, -6.0, -4.0, -3.0, -2.0, -1.0, -0.5,  0.5,  1.0,  2.0,  3.0,  4.0,  6.0, 40.0]
    hold_torque_v  = [-6, -2.85, -2.5, -2.25, -2, -1.65, -1, 1, 1.65, 2, 2.25, 2.5, 2.85, 6]
    hold_torque = np.interp(desired_angle, angle_bp, hold_torque_v)
    return hold_torque

  def get_steer_feedforward_function(self):
    if self.CP.flags & BmwFlags.SERVOTRONIC:
      return self.get_steer_feedforward_servotronic
    else:
      return self.get_steer_feedforward

  @staticmethod
  def _get_params(ret, candidate, fingerprint, car_fw, alpha_long, is_release, docs):
    ret.brand = "bmw"

    has_normal_cruise = 0x200 in fingerprint.get(CanBus.PT_CAN, {})
    has_dynamic_cruise = 0x193 in fingerprint.get(CanBus.PT_CAN, {})
    has_ldm = 0x0D5 in fingerprint.get(CanBus.PT_CAN, {})

    if (0x22F in fingerprint.get(CanBus.SERVO_CAN, {}) or
        0x22F in fingerprint.get(CanBus.AUX_CAN, {})):
      ret.flags |= BmwFlags.STEPPER_SERVO_CAN.value

    ret.openpilotLongitudinalControl = True
    ret.radarUnavailable = True
    ret.pcmCruise = False

    ret.autoResumeSng = False
    if has_normal_cruise:
      ret.flags |= BmwFlags.NORMAL_CRUISE_CONTROL.value
    elif has_dynamic_cruise:
      if not has_ldm:
        ret.flags |= BmwFlags.DYNAMIC_CRUISE_CONTROL.value
      else:
        ret.flags |= BmwFlags.ACTIVE_CRUISE_CONTROL_NO_ACC.value
        ret.autoResumeSng = True
    else:
      ret.flags |= BmwFlags.ACTIVE_CRUISE_CONTROL_NO_LDM.value
      ret.autoResumeSng = True

    if 0xb8 in fingerprint.get(CanBus.PT_CAN, {}) or 0xb5 in fingerprint.get(CanBus.PT_CAN, {}):
      ret.transmissionType = TransmissionType.automatic
    else:
      ret.transmissionType = TransmissionType.manual

    if 0xbc in fingerprint.get(CanBus.PT_CAN, {}):
      ret.steerRatio = 18.5

    if ret.flags & BmwFlags.DYNAMIC_CRUISE_CONTROL:
      ret.minEnableSpeed = 30. * CV.KPH_TO_MS
    if ret.flags & BmwFlags.NORMAL_CRUISE_CONTROL:
      ret.minEnableSpeed = 30. * CV.KPH_TO_MS

    ret.safetyConfigs = [get_safety_config(structs.CarParams.SafetyModel.bmw)]
    ret.safetyConfigs[0].safetyParam = 0

    ret.steerControlType = structs.CarParams.SteerControlType.torque
    ret.steerActuatorDelay = 0.4
    ret.steerLimitTimer = 0.4

    CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning, steering_angle_deadzone_deg=0.0)

    ret.longitudinalActuatorDelay = 0.3

    ret.centerToFront = ret.wheelbase * 0.44

    ret.startAccel = 0.0

    return ret
