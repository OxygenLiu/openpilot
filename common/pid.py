import numpy as np
from numbers import Number

class PIDController:
  def __init__(self, k_p, k_i, k_f=0., k_d=0., pos_limit=1e308, neg_limit=-1e308, rate=100):
    self._k_p = k_p
    self._k_i = k_i
    self._k_d = k_d
    self.k_f = k_f   # feedforward gain
    if isinstance(self._k_p, Number):
      self._k_p = [[0], [self._k_p]]
    if isinstance(self._k_i, Number):
      self._k_i = [[0], [self._k_i]]
    if isinstance(self._k_d, Number):
      self._k_d = [[0], [self._k_d]]

    self.set_limits(pos_limit, neg_limit)

    self.i_rate = 1.0 / rate
    self.speed = 0.0

    self.reset()

  @property
  def k_p(self):
    return np.interp(self.speed, self._k_p[0], self._k_p[1])

  @property
  def k_i(self):
    return np.interp(self.speed, self._k_i[0], self._k_i[1])

  @property
  def k_d(self):
    return np.interp(self.speed, self._k_d[0], self._k_d[1])

  def reset(self):
    self.p = 0.0
    self.i = 0.0
    self.d = 0.0
    self.f = 0.0
    self.control = 0

  def set_limits(self, pos_limit, neg_limit):
    self.pos_limit = pos_limit
    self.neg_limit = neg_limit

  def update(self, error, error_rate=0.0, speed=0.0, feedforward=0., freeze_integrator=False):
    self.speed = speed
    self.p = float(error) * self.k_p
    self.f = feedforward * self.k_f
    self.d = error_rate * self.k_d

    if not freeze_integrator:
      i = self.i + error * self.k_i * self.i_rate

      # Don't allow windup if already clipping
      test_control = self.p + i + self.d + self.f
      i_upperbound = self.i if test_control > self.pos_limit else self.pos_limit
      i_lowerbound = self.i if test_control < self.neg_limit else self.neg_limit
      self.i = np.clip(i, i_lowerbound, i_upperbound)

    control = self.p + self.i + self.d + self.f
    self.control = np.clip(control, self.neg_limit, self.pos_limit)
    return self.control


class OptimizedPIDController:
  """
  Enhanced PID Controller for BMW Lateral Control
  Features: derivative filtering, setpoint weighting, adaptive limits, bumpless transfer
  """
  def __init__(self, k_p, k_i, k_f=0., k_d=0.,
               pos_limit=1e308, neg_limit=-1e308, rate=100,
               # Enhanced features for BMW torque control
               derivative_filter_tau=0.02,   # 20ms derivative filter
               setpoint_weight_p=0.6,        # Proportional setpoint weighting
               error_deadband=0.002,         # 0.2% error deadband
               adaptive_integral=True,       # Adaptive integral limits
               bumpless_transfer=True):      # Bumpless transfer

    # Standard PID parameters with gain scheduling support
    self._k_p = k_p if isinstance(k_p, list) else [[0], [k_p]]
    self._k_i = k_i if isinstance(k_i, list) else [[0], [k_i]]
    self._k_d = k_d if isinstance(k_d, list) else [[0], [k_d]]
    self.k_f = k_f

    # Enhanced features
    self.derivative_filter_tau = derivative_filter_tau
    self.setpoint_weight_p = setpoint_weight_p
    self.error_deadband = error_deadband
    self.adaptive_integral = adaptive_integral
    self.bumpless_transfer = bumpless_transfer

    # Timing
    self.dt = 1.0 / rate
    self.speed = 0.0

    # Set limits
    self.set_limits(pos_limit, neg_limit)

    # Reset state
    self.reset()

  @property
  def k_p(self):
    return np.interp(self.speed, self._k_p[0], self._k_p[1])

  @property
  def k_i(self):
    return np.interp(self.speed, self._k_i[0], self._k_i[1])

  @property
  def k_d(self):
    return np.interp(self.speed, self._k_d[0], self._k_d[1])

  def reset(self):
    self.p = 0.0
    self.i = 0.0
    self.d = 0.0
    self.f = 0.0
    self.control = 0.0

    # Enhanced state variables
    self.last_setpoint = 0.0
    self.last_measurement = 0.0
    self.filtered_derivative = 0.0
    self.last_derivative_input = 0.0

  def set_limits(self, pos_limit, neg_limit):
    self.pos_limit = pos_limit
    self.neg_limit = neg_limit

  def _apply_deadband(self, error):
    """Apply deadband to reduce control chatter around setpoint"""
    if abs(error) < self.error_deadband:
      return 0.0
    return error

  def _calculate_adaptive_integral_limits(self, current_output):
    """Calculate adaptive integral limits to prevent windup"""
    if not self.adaptive_integral:
      return self.pos_limit, self.neg_limit

    # Adaptive limits based on remaining control authority
    remaining_pos = self.pos_limit - (current_output - self.i)
    remaining_neg = self.neg_limit - (current_output - self.i)

    return remaining_pos, remaining_neg

  def _filtered_derivative(self, measurement):
    """Calculate filtered derivative to reduce noise sensitivity"""
    # Derivative on measurement (avoids derivative kick)
    derivative_input = -measurement

    # Calculate raw derivative
    if hasattr(self, 'last_derivative_input'):
      raw_derivative = (derivative_input - self.last_derivative_input) / self.dt
    else:
      raw_derivative = 0.0

    self.last_derivative_input = derivative_input

    # Apply low-pass filter: alpha = dt / (tau + dt)
    alpha = self.dt / (self.derivative_filter_tau + self.dt)
    self.filtered_derivative = (1 - alpha) * self.filtered_derivative + alpha * raw_derivative

    return self.filtered_derivative

  def update(self, error, error_rate=0.0, speed=0.0, feedforward=0., freeze_integrator=False,
             setpoint=None, measurement=None):
    """
    Enhanced PID update with BMW-optimized features

    Args:
      error: Control error (setpoint - measurement)
      error_rate: Rate of error change (unused, kept for compatibility)
      speed: Current speed for gain scheduling
      feedforward: Feedforward signal
      freeze_integrator: Freeze integral action
      setpoint: Reference signal (for setpoint weighting)
      measurement: Process variable (for derivative on measurement)
    """
    self.speed = speed

    # Infer setpoint and measurement if not provided (backward compatibility)
    if setpoint is None and measurement is None:
      setpoint = self.last_setpoint
      measurement = setpoint - error
    elif setpoint is not None and measurement is None:
      measurement = setpoint - error
    elif setpoint is None and measurement is not None:
      setpoint = measurement + error

    # Apply error deadband to reduce control chatter
    effective_error = self._apply_deadband(error)

    # Proportional term with setpoint weighting
    if self.setpoint_weight_p < 1.0:
      # Reduce proportional action on setpoint changes (smoother response)
      weighted_error = self.setpoint_weight_p * setpoint - measurement
      self.p = self.k_p * weighted_error
    else:
      self.p = self.k_p * effective_error

    # Feedforward term (predictive control)
    self.f = feedforward * self.k_f

    # Derivative term with filtering (noise reduction)
    if measurement is not None:
      self.d = self.k_d * self._filtered_derivative(measurement)
    else:
      self.d = 0.0

    # Calculate preliminary output for adaptive integral limits
    preliminary_output = self.p + self.d + self.f

    # Integral term with adaptive windup protection
    if not freeze_integrator:
      integral_increment = effective_error * self.k_i * self.dt

      # Adaptive integral limits based on remaining control authority
      i_pos_limit, i_neg_limit = self._calculate_adaptive_integral_limits(preliminary_output)

      # Update integral with adaptive limits
      new_integral = self.i + integral_increment
      self.i = np.clip(new_integral, i_neg_limit, i_pos_limit)

    # Calculate total control output
    control = self.p + self.i + self.d + self.f

    # Apply output limits
    limited_control = np.clip(control, self.neg_limit, self.pos_limit)

    # Bumpless transfer: back-calculate integral if output is limited
    if self.bumpless_transfer and (limited_control != control):
      # Adjust integral to prevent windup and ensure smooth transitions
      self.i = limited_control - (self.p + self.d + self.f)
      control = limited_control

    # Store state for next iteration
    self.control = control
    self.last_setpoint = setpoint
    self.last_measurement = measurement

    return self.control
