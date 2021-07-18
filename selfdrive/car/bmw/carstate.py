from cereal import car
from common.numpy_fast import mean
from opendbc.can.can_define import CANDefine
from selfdrive.car.interfaces import CarStateBase
from opendbc.can.parser import CANParser
from selfdrive.config import Conversions as CV
from selfdrive.car.bmw.values import CAR, DBC, SteerActuatorParams, NO_DSU_CAR
from common.params import Params

class CarState(CarStateBase):
  def __init__(self, CP):
    super().__init__(CP)
    can_define = CANDefine(DBC[CP.carFingerprint]['pt'])
    self.shifter_values = can_define.dv["TransmissionDataDisplay"]['ShiftLeverPosition']
    self.angle_offset = 0.
    self.steer_warning = False
    self.low_speed_lockout = False
    self.steer_angle_delta_cmd = 0.
    self.steer_angle_cmd_motor= 0.
    self.steer_angle_cmd_motor_prev = 0.
    self.gas_kickdown = False

    self.is_metric = Params().get("IsMetric", encoding='utf8') == "1"
    self.cruise_plus = False
    self.cruise_minus = False
    self.cruise_plus5 = False
    self.cruise_minus5 = False
    self.cruise_resume = False
    self.cruise_cancel = False
    self.cruise_cancelUpStalk = False
    self.cruise_cancelDnStalk = False
    self.prev_cruise_plus = self.cruise_plus
    self.prev_cruise_minus = self.cruise_minus
    self.prev_cruise_plus5 = self.cruise_plus5
    self.prev_cruise_minus5 = self.cruise_minus5
    self.prev_cruise_resume = self.cruise_resume
    self.prev_cruise_cancel = self.cruise_cancel
    self.prev_cruise_cancelUpStalk = self.cruise_cancelUpStalk
    self.prev_cruise_cancelDnStalk = self.cruise_cancelDnStalk

    self.right_blinker_pressed = False
    self.left_blinker_pressed = False
    self.otherButtons = False
    self.prev_gasPressed = False

  def update(self, cp_PT, cp_F, cp_aux):
    # set these prev states at the beginning because they are used outside the update()
    self.prev_cruise_plus = self.cruise_plus
    self.prev_cruise_minus = self.cruise_minus
    self.prev_cruise_plus5 = self.cruise_plus5
    self.prev_cruise_minus5 = self.cruise_minus5
    self.prev_cruise_resume = self.cruise_resume
    self.prev_cruise_cancel = self.cruise_cancel
    self.prev_cruise_cancelUpStalk = self.cruise_cancelUpStalk
    self.prev_cruise_cancelDnStalk = self.cruise_cancelDnStalk

    ret = car.CarState.new_message()

    ret.doorOpen = False # not any([cp.vl["SEATS_DOORS"]['DOOR_OPEN_FL'], cp.vl["SEATS_DOORS"]['DOOR_OPEN_FR']
    ret.seatbeltUnlatched = False # not cp.vl["SEATS_DOORS"]['SEATBELT_DRIVER_UNLATCHED']

    ret.brakePressed = cp_PT.vl["EngineAndBrake"]['BrakePressed'] != 0
    ret.gas = cp_PT.vl['AccPedal']["AcceleratorPedalPercentage"]
    ret.gasPressed = cp_PT.vl['AccPedal']["AcceleratorPedalPressed"] != 0
    self.gas_kickdown = cp_PT.vl['AccPedal']["KickDownPressed"] != 0 #BMW has kickdown button at the bottom of the pedal

    ret.espDisabled = False  # cp.vl["ESP_CONTROL"]['TC_DISABLED'] ==1

    ret.vEgoRaw = cp_PT.vl['Speed']["VehicleSpeed"] * CV.KPH_TO_MS
    ret.wheelSpeeds.fl = ret.vEgoRaw  # cp_PT.vl["WHEEL_SPEEDS"]['WHEEL_SPEED_FL'] * CV.KPH_TO_MS #* speed_factor
    ret.wheelSpeeds.fr = ret.vEgoRaw  # cp_PT.vl["WHEEL_SPEEDS"]['WHEEL_SPEED_FR'] * CV.KPH_TO_MS #* speed_factor
    ret.wheelSpeeds.fl = ret.vEgoRaw  # cp_PT.vl["WHEEL_SPEEDS"]['WHEEL_SPEED_RL'] * CV.KPH_TO_MS #* speed_factor
    ret.wheelSpeeds.fr = ret.vEgoRaw  # cp_PT.vl["WHEEL_SPEEDS"]['WHEEL_SPEED_RR'] * CV.KPH_TO_MS #* speed_factor
    # ret.vEgoRaw = mean([ret.wheelSpeeds.fl, ret.wheelSpeeds.fr, ret.wheelSpeeds.rl, ret.wheelSpeeds.rr])

    ret.vEgo, ret.aEgo = self.update_speed_kf(ret.vEgoRaw)

    ret.standstill = not ret.vEgoRaw > 0.001

    ret.steeringRate = (cp_PT.vl["SteeringWheelAngle"]['SteeringSpeed'])
    can_gear = int(cp_PT.vl["TransmissionDataDisplay"]['ShiftLeverPosition'])
    ret.gearShifter = self.parse_gear_shifter(self.shifter_values.get(can_gear, None))
    blinker_on = cp_PT.vl["TurnSignals"]['TurnSignalActive'] != 0 and cp_PT.vl["TurnSignals"]['TurnSignalIdle'] == 0
    ret.leftBlinker = blinker_on and cp_PT.vl["TurnSignals"]['LeftTurn'] !=0   # blinking
    ret.rightBlinker = blinker_on and cp_PT.vl["TurnSignals"]['RightTurn'] !=0   # blinking
    self.right_blinker_pressed = not blinker_on and cp_PT.vl["TurnSignals"]['RightTurn'] != 0
    self.left_blinker_pressed = not blinker_on and cp_PT.vl["TurnSignals"]['LeftTurn'] != 0

    self.otherButtons = cp_PT.vl["SteeringButtons"]['Volume_DOWN'] !=0  or cp_PT.vl["SteeringButtons"]['Volume_UP'] !=0  or \
                        cp_PT.vl["SteeringButtons"]['Previous_down'] !=0  or cp_PT.vl["SteeringButtons"]['Next_up'] !=0 \
                        or self.prev_gasPressed and not ret.gasPressed # otherButtons used to indicate driver is engaged


    # emulate driver steering torque - allows lane change assist on blinker hold
    ret.steeringPressed = ret.gasPressed # E-series doesn't have torque sensor, so lightly pressing the gas indicates driver intention
    if ret.steeringPressed and ret.leftBlinker:
      ret.steeringTorque = 1
    elif ret.steeringPressed and  ret.rightBlinker:
      ret.steeringTorque = -1
    else:
      ret.steeringTorque = 0


    ret.cruiseState.available = True  # todo unless DCS (traction control) disabled
    if self.CP.carFingerprint in [CAR.E82_DCC, CAR.E90_DCC]: #_DCC implies the F-CAN bus has to be connected
      ret.steeringAngle = (cp_F.vl['SteeringWheelAngle_DSC']['SteeringPosition'])  # slightly quicker on F-CAN TODO find the factor and put in DBC
      ret.cruiseState.speed = cp_PT.vl["DynamicCruiseControlStatus"]['CruiseControlSetpointSpeed']
      ret.cruiseState.enabled = cp_PT.vl["DynamicCruiseControlStatus"]['CruiseActive'] != 0
      # if we are sending on F-can, we also need to read on F-can to differentiate our messages from car messages
      self.cruise_plus = cp_F.vl["CruiseControl"]['plus1mph_request'] != 0
      self.cruise_minus = cp_F.vl["CruiseControl"]['minus1mph_request'] != 0
      self.cruise_plus5 = cp_F.vl["CruiseControl"]['plus5mph_request'] != 0
      self.cruise_minus5 = cp_F.vl["CruiseControl"]['minus5mph_request'] != 0
      self.cruise_resume = cp_F.vl["CruiseControl"]['Resume_request'] != 0
      self.cruise_cancel = cp_F.vl["CruiseControl"]['Cancel_request_up_or_down_stalk'] != 0
      self.cruise_cancelUpStalk = cp_F.vl["CruiseControl"]['Cancel_request_up_stalk'] != 0
    elif self.CP.carFingerprint in [CAR.E82, CAR.E90]:
      ret.steeringAngle = (cp_PT.vl['SteeringWheelAngle']['SteeringPosition']) #  TODO find the factor and put in DBC
      ret.cruiseState.speed = cp_PT.vl["CruiseControlStatus"]['CruiseControlSetpointSpeed']
      ret.cruiseState.enabled = cp_PT.vl["CruiseControlStatus"]['CruiseCoontrolActiveFlag'] != 0
      self.cruise_plus = cp_PT.vl["CruiseControl"]['plus1mph_request'] != 0
      self.cruise_minus = cp_PT.vl["CruiseControl"]['minus1mph_request'] != 0
      self.cruise_plus5 = cp_PT.vl["CruiseControl"]['plus5mph_request'] != 0
      self.cruise_minus5 = cp_PT.vl["CruiseControl"]['minus5mph_request'] != 0
      self.cruise_resume = cp_PT.vl["CruiseControl"]['Resume_request'] != 0
      self.cruise_cancel = cp_PT.vl["CruiseControl"]['Cancel_request_up_or_down_stalk'] != 0
      self.cruise_cancelUpStalk = cp_PT.vl["CruiseControl"]['Cancel_request_up_stalk'] != 0

    self.cruise_cancelDnStalk = self.cruise_cancel and not self.cruise_cancelUpStalk

    if ret.cruiseState.enabled:
      if abs(ret.cruiseState.speed / ret.vEgoRaw - CV.MS_TO_KPH) < 0.3:
        self.is_metric = True
      elif abs(ret.cruiseState.speed / ret.vEgoRaw- CV.MS_TO_MPH) < 0.3:
        self.is_metric = False
    self.is_metric = True #TODO implement metric / imperial cruise control setting detection

    if self.is_metric: #recalculate to the right unit
      ret.cruiseState.speed = ret.cruiseState.speed * CV.KPH_TO_MS
    else:
      ret.cruiseState.speed = ret.cruiseState.speed * CV.MPH_TO_MS

    ret.genericToggle = cp_PT.vl["TransmissionDataDisplay"]['SportButtonState'] != 0
    ret.steeringTorqueEps = 0

    # steer_angle_cmd_motor is absolute value from open-loop stepper - can be used do detect lost steps
    # todo - is it actual openloop raw angle or target - target would be better for getting real target with all rate limits
    if cp_aux.vl['TMCL_actuatorStatus']['Command'] == 4: #Trinamic MVP command, convert steps to angle
       self.steer_angle_cmd_motor = cp_aux.vl['TMCL_actuatorStatus']['Value'] \
                             / SteerActuatorParams.ACTUATOR_RATIO / SteerActuatorParams.POSITION_SCALING
    self.steer_angle_delta_cmd = self.steer_angle_cmd_motor - self.steer_angle_cmd_motor_prev
    self.steer_angle_cmd_motor_prev = self.steer_angle_cmd_motor
    self.prev_gasPressed = ret.gasPressed
    return ret

  @staticmethod
  def get_can_parser(CP): #PT-CAN
    signals = [  # signal name, message name, default value
      # sig_name, sig_address, default
      ("BrakePressed", "EngineAndBrake", 0),
      ("ShiftLeverPosition", "TransmissionDataDisplay", 0),
      ("SportButtonState", "TransmissionDataDisplay", 0),
      ("AcceleratorPedalPressed", "AccPedal", 0),
      ("AcceleratorPedalPercentage", "AccPedal", 0),
      ("KickDownPressed", "AccPedal", 0),
      ("VehicleSpeed", "Speed", 0),
      # ("DOOR_OPEN_FL", "SEATS_DOORS", 1),
      # ("DOOR_OPEN_FR", "SEATS_DOORS", 1),
      # ("DOOR_OPEN_RL", "SEATS_DOORS", 1),
      # ("DOOR_OPEN_RR", "SEATS_DOORS", 1),
      # ("SEATBELT_DRIVER_UNLATCHED", "SEATS_DOORS", 1),
      ("SteeringPosition", "SteeringWheelAngle", 0),
      ("SteeringSpeed", "SteeringWheelAngle", 0),
      ("TurnSignalIdle", "TurnSignals", 0),
      ("TurnSignalActive", "TurnSignals", 0),
      ("RightTurn", "TurnSignals", 0),
      ("LeftTurn", "TurnSignals", 0),
      ("Volume_DOWN", "SteeringButtons", 0),
      ("Volume_UP", "SteeringButtons", 0),
      ("Previous_down", "SteeringButtons", 0),
      ("Next_up", "SteeringButtons", 0),
      ("Wheel1", "WheelSpeeds", 0),
      ("Wheel2", "WheelSpeeds", 0),
      ("Wheel3", "WheelSpeeds", 0),
      ("Wheel4", "WheelSpeeds", 0),
      # ("BRAKE_LIGHTS_ACC", "ESP_CONTROL", 0),
      ("minus1mph_request", "CruiseControl", 0),
      ("plus1mph_request", "CruiseControl", 0),
      ("minus5mph_request", "CruiseControl", 0),
      ("plus5mph_request", "CruiseControl", 0),
      ("Resume_request", "CruiseControl", 0),
      ("Cancel_request_up_stalk", "CruiseControl", 0),
      ("Cancel_request_up_or_down_stalk", "CruiseControl", 0),
    ]

    checks = [  # refresh frequency Hz  TODO measure with PCAN
      ("EngineAndBrake", 80),
      ("AccPedal", 33),
      ("Speed", 80),
      # ("SteeringWheelAngle", 80),
    ]

    if CP.carFingerprint in [CAR.E82_DCC, CAR.E90_DCC]:
      signals.append(("CruiseActive", "DynamicCruiseControlStatus", 0))
      signals.append(("CruiseControlSetpointSpeed", "DynamicCruiseControlStatus",
                      252))  # 252 is what's read when CC is not engaged, so it seems like a good default
      checks.append(("DynamicCruiseControlStatus", 33))
    elif CP.carFingerprint in [CAR.E82, CAR.E90]:
      signals.append(("CruiseCoontrolActiveFlag", "CruiseControlStatus", 0))
      signals.append(("CruiseControlSetpointSpeed", "CruiseControlStatus", 0))
      checks.append(("CruiseControlStatus", 33))

    return CANParser(DBC[CP.carFingerprint]['pt'], signals, checks, 0)  # 0: PT-CAN

  @staticmethod
  def get_cam_can_parser(CP):  # 540 vehicle option could get away with just PT_CAN, but vo544 requires sending, and thus receiving, cruise commands on F-CAN. F-can works for both options.
    signals = [  # signal name, message name, default value
      ("SteeringPosition", "SteeringWheelAngle_DSC", 0), #this is slightly faster than PT-CAN equivalent
      # some F-can messages, like CruiseControl, are mirrored on PT-CAN
      ("minus1mph_request", "CruiseControl", 0),
      ("plus1mph_request", "CruiseControl", 0),
      ("minus5mph_request", "CruiseControl", 0),
      ("plus5mph_request", "CruiseControl", 0),
      ("Resume_request", "CruiseControl", 0),
      ("Cancel_request_up_stalk", "CruiseControl", 0),
      ("Cancel_request_up_or_down_stalk", "CruiseControl", 0),
    ]

    if CP.carFingerprint in [CAR.E82_DCC, CAR.E90_DCC]:
      checks = [  # refresh frequency Hz  TODO measure with PCAN
      ("SteeringWheelAngle_DSC", 80), ]
    else:
      checks = []

    return CANParser(DBC[CP.carFingerprint]['pt'], signals, checks, 1)  # 1: F-CAN,

  @staticmethod
  def get_actuator_can_parser(CP):
    signals = [
      ("Command", "TMCL_actuatorStatus", 0),
      ("Value", "TMCL_actuatorStatus", 0),
    ]
    checks = [ ] # refresh frequency Hz
      # ("TMCL_actuatorStatus", 100), ] don't check refreshing because when steering not active, the messages are not returned
    return CANParser('OpenActuator', signals, checks, 2)  # 2: Actuator-CAN,
