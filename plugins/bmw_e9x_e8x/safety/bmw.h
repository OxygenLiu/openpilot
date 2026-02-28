#pragma once

#include "opendbc/safety/declarations.h"

// CAN msgs we care about
#define BMW_EngineAndBrake 0xA8U
#define BMW_AccPedal 0xAAU
#define BMW_Speed 0x1A0U
#define BMW_SteeringWheelAngle_slow 0xC8U
#define BMW_CruiseControlStatus 0x200U
#define BMW_DynamicCruiseControlStatus 0x193U
#define BMW_CruiseControlStalk 0x194U
#define BMW_TransmissionDataDisplay 0x1D2U

// BMW Stepper Servo CAN Messages
#define STEPPER_STEERING_COMMAND 0x22eU
#define STEPPER_STEERING_STATUS 0x22fU

#define BMW_PT_CAN 0U
#define BMW_F_CAN 1U
#define BMW_AUX_CAN 2U

#define CAN_BMW_SPEED_FAC 0.1
#define CAN_BMW_ACC_FAC 0.025
#define CAN_ACTUATOR_TQ_FAC 0.125

static float bmw_speed = 0.0f;


static void bmw_rx_hook(const CANPacket_t *msg) {
  int addr = msg->addr;
  int bus = msg->bus;

  if (addr == BMW_DynamicCruiseControlStatus) { // VO544 
    bool cruise_engaged = (((msg->data[5] >> 3) & 0x1U) == 1U);
    pcm_cruise_check(cruise_engaged);
  } else if (addr == BMW_CruiseControlStatus) { // VO540 
    bool cruise_engaged = (((msg->data[1] >> 5) & 0x1U) == 1U);
    pcm_cruise_check(cruise_engaged);
  }

  // BMW cruise stalk cancel handling moved to openpilot CarState

  // BMW TransmissionDataDisplay not needed for safety
  // BMW's own cruise control system handles gear position requirements

  // get vehicle speed
  if (addr == BMW_Speed) {
    uint32_t speed_raw = (msg->data[0] << 8) | msg->data[1];  // Get bytes 0-1
    // raw to km/h to m/s
    bmw_speed = to_signed(speed_raw & 0xFFFU, 12) * CAN_BMW_SPEED_FAC * KPH_TO_MS;

    // check moving forward and reverse
    vehicle_moving = (msg->data[1] & 0x30U) != 0U;

    // // check lateral acceleration limits
    // uint32_t lat_acc_raw = (((msg->data[3] << 8) | msg->data[4]) >> 4) & 0xFFF;  // Extract 12 bits from bytes 3-4
    // float bmw_lat_acc = to_signed(lat_acc_raw, 12) * CAN_BMW_ACC_FAC;
    // if (ABS(bmw_lat_acc) > BMW_LAT_ACC_MAX) {
    //   print("Too big lateral acc \n");
    //   controls_allowed = false; //todo add soft-off request when violation occurs to loss of torque in the turn
    // }
  }

  // STEPPER_SERVO_CAN: get STEERING_STATUS
  if ((addr == STEPPER_STEERING_STATUS) && ((bus == BMW_F_CAN) || (bus == BMW_AUX_CAN))) {
    int8_t torque_meas_new = (int8_t)(msg->data[2]); // torque raw
    update_sample(&torque_meas, torque_meas_new);

    // SOFT_OFF status is monitored by openpilot for UI warnings
    // Stepper servo auto-recovers on next command - no panda intervention needed
  }

  // BMW E90 uses torque-controlled steering only - no angle monitoring needed

  // BMW brake detection (disengagement handled by Panda for safety)
  if (addr == BMW_EngineAndBrake) {
    brake_pressed = (msg->data[7] & 0x20U) != 0U;
  }

  if (addr == BMW_AccPedal) {
    gas_pressed = (msg->data[6] & 0x30U) != 0U;
  }

  // BMW E8x/E9x dummy states for generic_rx_checks() compatibility
  // No steering torque sensor for override detection
  steering_disengage = false;

  // No regen braking paddle in BMW E8x/E9x
  regen_braking = false;

}

static bool bmw_tx_hook(const CANPacket_t *msg) {
  int addr = msg->addr;

  const TorqueSteeringLimits STEPPER_SERVO_LIMITS = {
    .max_torque = (12.f / CAN_ACTUATOR_TQ_FAC),     // < 12Nm
    .dynamic_max_torque = true,
    .max_torque_lookup = {
      {15., 22., 28.},    // m/s: 54kph, 80kph, 100kph
      {96, 64, 32},       // CAN units: 12Nm, 8Nm, 4Nm (full torque up to 54kph)
    },
    .max_rate_up = 2,                               // <= 0.125Nm/10ms
    .max_rate_down = (1.0f / CAN_ACTUATOR_TQ_FAC),  // < 1Nm/10ms
    .max_rt_delta = (25.0f / CAN_ACTUATOR_TQ_FAC),  // 25Nm/250ms
    .max_torque_error = (1.0f / CAN_ACTUATOR_TQ_FAC),  // 1Nm
    .type = TorqueMotorLimited,
  };

  bool tx = true;
  
  // STEPPER_SERVO_CAN: BMW E90 torque control only
  if (addr == STEPPER_STEERING_COMMAND) {
    // Torque Control Mode:
    uint8_t steer_mode = (msg->data[1] >> 4) & 0b11u;
    if (steer_mode != 0x0U) {
      int8_t steer_torque = (int8_t)(msg->data[4]); // Nm / CAN_ACTUATOR_TQ_FAC
      
      // BMW stepper servo: treat any non-zero torque as steer request
      int steer_req = (steer_torque != 0) ? 1 : 0;
      if (steer_torque_cmd_checks(steer_torque, steer_req, STEPPER_SERVO_LIMITS)) {
        tx = false;
      }
    }
    // Always allow mode 0 (disabled) commands for safety
  }

  return tx;
}

static safety_config bmw_init(uint16_t param) {
  SAFETY_UNUSED(param);
  
  static RxCheck bmw_rx_checks[] = {
    // Core safety: brake, gas, speed on Bus 0, steering torque on Bus 1 (same pattern as Toyota 0xaa, 0x260, 0x1D2, 0x226)
    {.msg = {{BMW_EngineAndBrake, BMW_PT_CAN, 8, .frequency = 100U,
              .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{BMW_AccPedal, BMW_PT_CAN, 8, .frequency = 100U,
              .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{BMW_Speed, BMW_PT_CAN, 8, .frequency = 50U,
              .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{BMW_DynamicCruiseControlStatus, BMW_PT_CAN, 8, .frequency = 5U,
              .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, 
             {BMW_CruiseControlStatus, BMW_PT_CAN, 8, .frequency = 5U,
              .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true},
             { 0 }}},
    {.msg = {{STEPPER_STEERING_STATUS,  BMW_F_CAN, 8, .ignore_counter = true, .frequency = 100U, 
              .ignore_quality_flag = true, .ignore_checksum = true},
             {STEPPER_STEERING_STATUS,  BMW_AUX_CAN, 8, .ignore_counter = true, .frequency = 100U,
              .ignore_quality_flag = true, .ignore_checksum = true},
             { 0 }}},
  };

  // TX_MSGS configuration - allowed outgoing CAN messages
  static const CanMsg BMW_TX_MSGS[] = {
    {BMW_CruiseControlStalk, BMW_PT_CAN, 4, .check_relay = false}, // Normal cruise control send status on PT-CAN
    {BMW_CruiseControlStalk, BMW_F_CAN, 4, .check_relay = false}, // Dynamic cruise control send status on F-CAN
    {STEPPER_STEERING_COMMAND, BMW_F_CAN, 5, .check_relay = false}, // STEPPER_SERVO_CAN is allowed on F-CAN network
    {STEPPER_STEERING_COMMAND, BMW_AUX_CAN, 5, .check_relay = false},  // or an standalone network
  };

  bmw_speed = 0.0f;

  safety_config ret = BUILD_SAFETY_CFG(bmw_rx_checks, BMW_TX_MSGS);
  ret.disable_forwarding = true;   
  
  return ret;
}

const safety_hooks bmw_hooks = {
  .init = bmw_init,
  .rx = bmw_rx_hook,
  .tx = bmw_tx_hook,
  .fwd = NULL,              
  .get_counter = NULL,          
  .get_checksum = NULL,         
  .compute_checksum = NULL,     
  .get_quality_flag_valid = NULL, 
};
