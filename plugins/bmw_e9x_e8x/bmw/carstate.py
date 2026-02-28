import numpy as np
from opendbc.can import CANDefine, CANParser
from opendbc.car import Bus, structs, create_button_events
from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.interfaces import CarStateBase
from bmw.values import DBC, CanBus, BmwFlags, CruiseSettings
import cereal.messaging as messaging

ButtonType = structs.CarState.ButtonEvent.Type

# Resume button hold duration threshold (in frames at 100Hz = 10ms per frame)
# 0.5 seconds = 50 frames, but counter increments 49 times (reset to 0 on first press, then 49 increments)
# So threshold is 49 to detect 50 frames (0.5 seconds) of button press
# This makes personality cycling more practical (easier to hold for 0.5s than 1s)
RESUME_LONG_PRESS_FRAMES = 49


class CarState(CarStateBase):
  def __init__(self, CP):
    super().__init__(CP)
    can_define = CANDefine(DBC[CP.carFingerprint]['pt'])
    self.shifter_values = can_define.dv["TransmissionDataDisplay"]['ShiftLeverPosition']
    self.gas_kickdown = False

    self.cluster_min_speed = CruiseSettings.CLUSTER_OFFSET

    self.is_metric = None
    self.cruise_stalk_speed = 0
    self.cruise_stalk_resume = False
    self.cruise_stalk_cancel = False
    self.cruise_stalk_cancel_up = False
    self.cruise_stalk_cancel_dn = False
    self.cruise_stalk_counter = 0
    self.prev_cruise_stalk_speed = 0
    self.prev_cruise_stalk_resume = self.cruise_stalk_resume
    self.prev_cruise_stalk_cancel = self.cruise_stalk_cancel
    self.prev_cruise_enabled = False  # Track previous openpilot cruise state for resume button logic
    self.resume_button_hold_frames = 0  # Track how many frames resume button has been held (v4 duration-based logic)

    self.right_blinker_pressed = False
    self.left_blinker_pressed = False
    self.other_buttons = False
    self.prev_other_buttons = False
    self.prev_gas_pressed = False
    self.dtc_mode = False

    # Subscribe to radarState and liveDelay for velocity-difference-based T_FOLLOW scaling
    self.sm = messaging.SubMaster(['radarState', 'liveDelay'])

  def update(self, can_parsers) -> structs.CarState:
    cp_PT = can_parsers[Bus.pt]
    cp_F = can_parsers[Bus.body]
    cp_aux = can_parsers[Bus.alt]

    ret = structs.CarState()

    # set these prev states at the beginning because they are used outside the update()
    self.prev_cruise_stalk_speed = self.cruise_stalk_speed
    self.prev_cruise_stalk_resume = self.cruise_stalk_resume
    self.prev_cruise_stalk_cancel = self.cruise_stalk_cancel

    ret.doorOpen = False
    ret.seatbeltUnlatched = False

    ret.brakePressed = cp_PT.vl["EngineAndBrake"]['BrakePressed'] != 0
    ret.parkingBrake = cp_PT.vl["Status_contact_handbrake"]["Handbrake_pulled_up"] != 0
    ret.gasPressed = cp_PT.vl['AccPedal']["AcceleratorPedalPressed"] != 0 or cp_PT.vl['AccPedal']["KickDownPressed"] != 0
    self.gas_kickdown = cp_PT.vl['AccPedal']["KickDownPressed"] != 0

    ret.vEgoRaw = cp_PT.vl['Speed']["VehicleSpeed"] * CV.KPH_TO_MS
    ret.vEgo, ret.aEgo = self.update_speed_kf(ret.vEgoRaw)
    ret.vEgoCluster = ret.vEgo + CruiseSettings.CLUSTER_OFFSET * CV.KPH_TO_MS
    ret.standstill = not cp_PT.vl['Speed']["MovingForward"] and not cp_PT.vl['Speed']["MovingReverse"]
    ret.yawRate = cp_PT.vl['Speed']["YawRate"] * CV.DEG_TO_RAD
    ret.steeringRateDeg = cp_PT.vl["SteeringWheelAngle"]['SteeringSpeed']
    can_gear = int(cp_PT.vl["TransmissionDataDisplay"]['ShiftLeverPosition'])
    ret.gearShifter = self.parse_gear_shifter(self.shifter_values.get(can_gear, None))

    blinker_on = cp_PT.vl["TurnSignals"]['TurnSignalActive'] != 0 and cp_PT.vl["TurnSignals"]['TurnSignalIdle'] == 0
    ret.leftBlinker = blinker_on and cp_PT.vl["TurnSignals"]['LeftTurn'] != 0
    ret.rightBlinker = blinker_on and cp_PT.vl["TurnSignals"]['RightTurn'] != 0
    self.right_blinker_pressed = not blinker_on and cp_PT.vl["TurnSignals"]['RightTurn'] != 0
    self.left_blinker_pressed = not blinker_on and cp_PT.vl["TurnSignals"]['LeftTurn'] != 0

    self.dtc_mode = cp_PT.vl['StatusDSC_KCAN']['DTC_on'] != 0

    self.other_buttons = \
      cp_PT.vl["SteeringButtons"]['Volume_DOWN'] != 0 or cp_PT.vl["SteeringButtons"]['Volume_UP'] != 0 or \
      cp_PT.vl["SteeringButtons"]['Previous_down'] != 0 or cp_PT.vl["SteeringButtons"]['Next_up'] != 0 or \
      cp_PT.vl["SteeringButtons"]['VoiceControl'] != 0 or \
      self.prev_gas_pressed and not ret.gasPressed

    ret.steeringPressed = cp_PT.vl["SteeringButtons"]['VoiceControl'] != 0 or ret.gasPressed
    if ret.steeringPressed and ret.leftBlinker:
      ret.steeringTorque = 1
    elif ret.steeringPressed and ret.rightBlinker:
      ret.steeringTorque = -1
    else:
      ret.steeringTorque = 0

    ret.espDisabled = cp_PT.vl['StatusDSC_KCAN']['DSC_full_off'] != 0
    ret.cruiseState.available = not ret.espDisabled
    ret.cruiseState.nonAdaptive = False

    cruise_control_stal_msg = cp_PT.vl["CruiseControlStalk"]
    if self.CP.flags & BmwFlags.DYNAMIC_CRUISE_CONTROL:
      ret.steeringAngleDeg = cp_F.vl['SteeringWheelAngle_DSC']['SteeringPosition']
      ret.cruiseState.speed = cp_PT.vl["DynamicCruiseControlStatus"]['CruiseControlSetpointSpeed'] * (CV.KPH_TO_MS if self.is_metric else CV.MPH_TO_MS)
      ret.cruiseState.enabled = cp_PT.vl["DynamicCruiseControlStatus"]['CruiseActive'] != 0
      cruise_control_stal_msg = cp_F.vl["CruiseControlStalk"]
    elif self.CP.flags & BmwFlags.NORMAL_CRUISE_CONTROL:
      ret.steeringAngleDeg = cp_PT.vl['SteeringWheelAngle']['SteeringPosition']
      ret.cruiseState.speed = cp_PT.vl["CruiseControlStatus"]['CruiseControlSetpointSpeed'] * (CV.KPH_TO_MS if self.is_metric else CV.MPH_TO_MS)
      ret.cruiseState.enabled = cp_PT.vl["CruiseControlStatus"]['CruiseControlActiveFlag'] != 0
    ret.cruiseState.speedCluster = ret.cruiseState.speed + CruiseSettings.CLUSTER_OFFSET * CV.KPH_TO_MS
    if cruise_control_stal_msg['plus1'] != 0:
      self.cruise_stalk_speed = 1
    elif cruise_control_stal_msg['minus1'] != 0:
      self.cruise_stalk_speed = -1
    elif cruise_control_stal_msg['plus5'] != 0:
      self.cruise_stalk_speed = 5
    elif cruise_control_stal_msg['minus5'] != 0:
      self.cruise_stalk_speed = -5
    else:
      self.cruise_stalk_speed = 0
    self.cruise_stalk_resume = cruise_control_stal_msg['resume'] != 0
    self.cruise_stalk_cancel = cruise_control_stal_msg['cancel'] != 0
    self.cruise_stalk_cancel_up = cruise_control_stal_msg['cancel_lever_up'] != 0
    self.cruise_stalk_counter = cruise_control_stal_msg['Counter_0x194']
    self.cruise_stalk_cancel_dn = self.cruise_stalk_cancel and not self.cruise_stalk_cancel_up

    if self.is_metric is None and ret.cruiseState.enabled and ret.vEgo > 5:
      speed_ratio = ret.cruiseState.speed / ret.vEgo
      if 0.8 < speed_ratio < 1.2:
        self.is_metric = False
      elif 0.8 * CV.MPH_TO_KPH < speed_ratio < 1.2 * CV.MPH_TO_KPH:
        self.is_metric = True
        cruise_msg = "DynamicCruiseControlStatus" if (self.CP.flags & BmwFlags.DYNAMIC_CRUISE_CONTROL) else "CruiseControlStatus"
        ret.cruiseState.speed = cp_PT.vl[cruise_msg]['CruiseControlSetpointSpeed'] * CV.KPH_TO_MS
      else:
        ret.accFaulted = True

    ret.genericToggle = self.dtc_mode

    ret.coolantTemp = cp_PT.vl["EngineData"]["TEMP_ENG"]
    ret.oilTemp = cp_PT.vl["EngineData"]["TEMP_EOI"]

    if self.CP.flags & BmwFlags.STEPPER_SERVO_CAN:
      ret.steeringTorqueEps = cp_aux.vl['STEERING_STATUS']['STEERING_TORQUE']
      ret.steeringAngleOffsetDeg = ret.steeringAngleDeg - cp_aux.vl['STEERING_STATUS']['STEERING_ANGLE']
      ret.steerFaultTemporary = (int(cp_aux.vl['STEERING_STATUS']['DEBUG_STATES']) & 0x20) != 0
      ret.steerFaultTemporary |= (int(cp_aux.vl['STEERING_STATUS']['DEBUG_STATES']) & 0x40) != 0
      ret.steerFaultTemporary |= (int(cp_aux.vl['STEERING_STATUS']['DEBUG_STATES']) & 0x80) != 0
      ret.steerFaultTemporary |= (int(cp_aux.vl['STEERING_STATUS']['CONTROL_STATUS']) & 0x4) != 0

    self.prev_gas_pressed = ret.gasPressed

    # Resume button duration-based logic (v6)
    resume_button_events = []

    if self.cruise_stalk_resume:
      if not self.prev_cruise_stalk_resume:
        self.resume_button_hold_frames = 0
      else:
        self.resume_button_hold_frames += 1
    else:
      self.resume_button_hold_frames = 0

    if self.cruise_stalk_resume and not self.prev_cruise_stalk_resume:
      resume_button_events.append(structs.CarState.ButtonEvent(
        pressed=True,
        type=ButtonType.resumeCruise
      ))

    elif not self.cruise_stalk_resume and self.prev_cruise_stalk_resume:
      if self.resume_button_hold_frames >= RESUME_LONG_PRESS_FRAMES:
        resume_button_events.append(structs.CarState.ButtonEvent(
          pressed=True,
          type=ButtonType.gapAdjustCruise
        ))
        resume_button_events.append(structs.CarState.ButtonEvent(
          pressed=False,
          type=ButtonType.gapAdjustCruise
        ))
      else:
        resume_button_events.append(structs.CarState.ButtonEvent(
          pressed=False,
          type=ButtonType.resumeCruise
        ))

    ret.buttonEvents = [
      *create_button_events(self.cruise_stalk_speed > 0, self.prev_cruise_stalk_speed > 0, {1: ButtonType.accelCruise}),
      *create_button_events(self.cruise_stalk_speed < 0, self.prev_cruise_stalk_speed < 0, {1: ButtonType.decelCruise}),
      *create_button_events(self.cruise_stalk_cancel, self.prev_cruise_stalk_cancel, {1: ButtonType.cancel}),
      *create_button_events(self.other_buttons, self.prev_other_buttons, {1: ButtonType.altButton2}),
      *resume_button_events
      ]

    self.cruise_state_enabled = ret.cruiseState.enabled
    self.prev_cruise_enabled = ret.cruiseState.enabled
    self.prev_other_buttons = self.other_buttons
    return ret

  def update_button_enable(self, buttonEvents: list[structs.CarState.ButtonEvent]):
    if self.cruise_state_enabled and not self.out.cruiseState.enabled:
      return True
    return False

  @staticmethod
  def get_can_parsers(CP):
    pt_messages = [
      ("TurnSignals", float('nan')),
      ("Status_contact_handbrake", float('nan')),
      ("EngineData", float('nan')),
    ]

    fcan_messages = []
    if CP.flags & BmwFlags.DYNAMIC_CRUISE_CONTROL:
      fcan_messages.append(("CruiseControlStalk", float('nan')))

    servo_can_messages = []

    return {
      Bus.pt: CANParser(DBC[CP.carFingerprint][Bus.pt], pt_messages, CanBus.PT_CAN),
      Bus.body: CANParser(DBC[CP.carFingerprint][Bus.body], fcan_messages, CanBus.F_CAN),
      Bus.alt: CANParser('ocelot_controls', servo_can_messages, CanBus.SERVO_CAN),
    }
