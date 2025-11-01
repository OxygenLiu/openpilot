"""
Base class for all personalized learning features

Implements common 6-step learning pattern:
1. Data collection - Collect data points from cereal messages
2. Validation - Check quality criteria (speed, continuity, etc.)
3. Accumulation - Add valid segments/blocks to circular buffer
4. Estimation - Calculate mean + std when sufficient data
5. Activation - Use learned parameters in openpilot
6. Update - Newest data replaces oldest (sliding window)
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
from collections import deque
from functools import partial
import numpy as np
from cereal import car
from openpilot.selfdrive.locationd.helpers import fft_next_good_size, parabolic_peak_interp


class LearnerClass(ABC):
    """
    Abstract base class for personalized learning features

    All learners must implement:
    - handle_log(): Process incoming cereal messages (20Hz)
    - update(): Update learner state (20Hz)
    - update_estimate(): Update parameter estimates (4Hz)
    - get_msg_data(): Get data for liveDelay message
    - reset(): Reset with loaded data from persistent storage
    - get_serialization_data(): Get data for serialization
    """

    # Required class attribute: set of required cereal messages
    inputs: set[str]

    def __init__(self, CP: car.CarParams, dt: float = 0.05):
        """
        Initialize learner

        Args:
            CP: CarParams (vehicle-specific configuration)
            dt: Update interval in seconds (default 20Hz = 0.05s)
        """
        self.CP = CP
        self.dt = dt
        self.t = 0.0

    @abstractmethod
    def handle_log(self, t: float, which: str, msg):
        """
        Handle incoming cereal message (called at 20Hz)

        Args:
            t: Current time in seconds
            which: Message type (e.g., "carState", "radarState")
            msg: Cereal message object

        Note:
            This method should update internal state based on message data.
            It should NOT perform heavy computations - those belong in update().
        """
        pass

    @abstractmethod
    def update(self, t: float):
        """
        Update learner state (called at 20Hz)

        Performs:
        - Data validation based on quality criteria
        - Segment/block accumulation to circular buffer
        - Circular buffer updates (oldest replaced when full)

        Args:
            t: Current time in seconds

        Note:
            This is where the main learning logic happens.
            Heavy computations are acceptable here.
        """
        pass

    @abstractmethod
    def update_estimate(self):
        """
        Update parameter estimates (called at 4Hz)

        Performs:
        - Mean + std calculation from circular buffer
        - Status update (unlearned/learning/learned)
        - Confidence estimation

        Note:
            This is typically lighter than update() since it only
            recalculates statistics from already-collected data.
        """
        pass

    @abstractmethod
    def get_msg_data(self) -> Dict[str, Any]:
        """
        Get data for liveDelay message publication

        Returns:
            Dict mapping field names to values for liveDelay message.
            All fields will be set via setattr(liveDelay, key, value).

        Example:
            {
                'parameterName': 1.5,           # Learned parameter value
                'parameterNameStd': 0.1,        # Standard deviation (confidence)
                'validBlocks': 25,              # Number of valid data blocks
                'progress': 50,                 # Progress 0-100%
                'status': PersonalizedStatus.learning,  # Learning status
            }

        Note:
            Field names must match cereal log.capnp liveDelay struct.
        """
        pass

    @abstractmethod
    def reset(self, *args):
        """
        Reset learner with loaded data from persistent storage

        Args:
            *args: Learner-specific loaded data (varies by learner)
                   Examples:
                   - Delay learner: (delay_estimate, valid_blocks)
                   - T_FOLLOW: (scales, valid_blocks, block_data)
                   - Curve speed: (segment_data, valid_segments)

        Note:
            This method is called once at initialization if persistent
            data exists. It should reconstruct the learner's state from
            saved data, including raw blocks/segments for proper std calculation.
        """
        pass

    @abstractmethod
    def get_serialization_data(self) -> Dict[str, Any]:
        """
        Get data for serialization to persistent storage

        Returns:
            Dict with all data needed to reconstruct learner state.
            This data will be saved to /data/params/d/LiveDelay.

        Example:
            {
                'scales': [1.0, 1.1, 1.2],      # Learned parameters
                'valid_blocks': [10, 15, 20],   # Valid data counts
                'raw_block_data': np.array(...), # Raw data for std calculation
            }

        Note:
            Must include RAW data (blocks/segments), not just means!
            This ensures proper std calculation after reload.
        """
        pass

    def is_learned(self) -> bool:
        """
        Check if learner has sufficient data for confident estimates

        Returns:
            True if learned (sufficient data), False otherwise

        Note:
            Subclasses should override this if they have specific
            learning thresholds. Default implementation raises NotImplementedError.
        """
        raise NotImplementedError("Subclass must implement is_learned()")


class BlockAverage:
    """
    Circular buffer for block-averaged data with persistence support

    Used by delay learners and T_FOLLOW learner for aggregating
    high-frequency samples into blocks for robust statistics.

    Algorithm:
    1. Accumulate samples within a block (e.g., 100 samples)
    2. When block full, compute block average
    3. Store block average in circular buffer
    4. Calculate mean + std of block averages
    """

    def __init__(self, num_blocks: int, block_size: int, valid_blocks: int, initial_value: float):
        """
        Initialize BlockAverage circular buffer

        Args:
            num_blocks: Total number of blocks (buffer size, e.g., 50)
            block_size: Samples per block (e.g., 100)
            valid_blocks: Number of valid blocks already collected (0-num_blocks)
            initial_value: Initial value for all blocks
        """
        self.num_blocks = num_blocks
        self.block_size = block_size
        self.valid_blocks = min(valid_blocks, num_blocks)
        self.block_idx = self.valid_blocks % num_blocks  # Current block being filled

        # Block averages storage: shape (num_blocks, 1)
        self.values = np.tile(initial_value, (num_blocks, 1))

        # Current block accumulation
        self.idx = 0  # Sample index within current block (0-block_size)

    def update(self, value: float):
        """
        Add sample to current block (running average)

        Args:
            value: Sample value to add

        Note:
            When block full (idx == block_size), moves to next block
            and increments valid_blocks (saturates at num_blocks).
        """
        # Update running average for current block
        self.values[self.block_idx] = (self.idx * self.values[self.block_idx] + value) / (self.idx + 1)
        self.idx = (self.idx + 1) % self.block_size

        # Block complete - move to next block
        if self.idx == 0:
            self.block_idx = (self.block_idx + 1) % self.num_blocks  # Circular
            self.valid_blocks = min(self.valid_blocks + 1, self.num_blocks)  # Saturate

    def get(self) -> tuple[float, float, float, float]:
        """
        Calculate statistics from valid blocks

        Returns:
            Tuple: (mean, std, mean_absolute_error, max_value)

        Note:
            Excludes current block being filled (block_idx) from statistics.
        """
        # Get valid block indices (exclude current block being filled)
        valid_block_idx = [i for i in range(self.valid_blocks) if i != self.block_idx]

        if len(valid_block_idx) == 0:
            # No complete blocks yet
            return (np.nan, np.nan, np.nan, np.nan)

        # Calculate statistics from valid blocks
        valid_blocks = self.values[valid_block_idx]
        valid_mean = float(np.mean(valid_blocks))
        valid_std = float(np.std(valid_blocks))
        mean_absolute_error = float(np.mean(np.abs(valid_blocks - valid_mean)))
        max_value = float(np.max(valid_blocks))

        return (valid_mean, valid_std, mean_absolute_error, max_value)

    def get_block_data(self) -> np.ndarray:
        """
        Get raw block values for serialization (only valid blocks)

        Returns:
            Array of shape (valid_blocks, 1) with raw block averages

        Note:
            This is critical for persistence! Without raw block data,
            std calculation becomes meaningless after reload.
        """
        return self.values[:self.valid_blocks].copy()

    @staticmethod
    def from_block_data(num_blocks: int, block_size: int, valid_blocks: int, block_data: np.ndarray):
        """
        Create BlockAverage from serialized block data

        Args:
            num_blocks: Total buffer size
            block_size: Samples per block
            valid_blocks: Number of valid blocks in block_data
            block_data: Array of shape (valid_blocks, 1) with raw block values

        Returns:
            BlockAverage instance with restored block values

        Note:
            This ensures proper std calculation after deserialization.
            Without raw block data, all blocks would be initialized to
            the same value (mean), resulting in std=0.
        """
        # Calculate initial value from block data mean
        initial_value = float(np.mean(block_data)) if len(block_data) > 0 else 1.0

        # Create BlockAverage with default initialization
        ba = BlockAverage(num_blocks, block_size, valid_blocks, initial_value)

        # Restore actual block values (NOT all same!)
        if len(block_data) > 0:
            loaded_count = min(len(block_data), num_blocks)
            ba.values[:loaded_count] = block_data[:loaded_count].reshape(-1, 1)

        return ba


class CurveSegmentBuffer:
    """
    Circular buffer for curve speed segment data with persistence support

    Used by CurveSpeedLearner for storing 50 most recent curve segments.
    Each segment contains 4 extracted parameters from manual curve driving.

    Algorithm:
    1. Collect data during 10+ second curve segment
    2. Extract 4 parameters (lookahead, lat_accel, margin, min_curv)
    3. Store segment in circular buffer (oldest replaced when full)
    4. Calculate median + std of all parameters
    """

    SEGMENTS_NEEDED = 50  # Total segments to store (sliding window)

    def __init__(self, valid_segments: int = 0, segment_data: Optional[np.ndarray] = None):
        """
        Initialize CurveSegmentBuffer circular buffer

        Args:
            valid_segments: Number of segments collected (0-50)
            segment_data: Pre-loaded segment data [valid_segments, 4] array
                          Format: [lookahead_time, lat_accel_limit, speed_margin, min_curvature]
        """
        self.num_segments = self.SEGMENTS_NEEDED
        self.valid_segments = min(valid_segments, self.num_segments)
        self.segment_idx = self.valid_segments % self.num_segments  # Current write position

        # Segment data storage: [num_segments, 4] array
        # Each row: [lookahead_time, lat_accel_limit, speed_margin, min_curvature]
        if segment_data is not None and len(segment_data) > 0:
            # Load from persistent storage
            self.segments = np.zeros((self.num_segments, 4), dtype=np.float32)
            loaded_count = min(len(segment_data), self.num_segments)
            self.segments[:loaded_count] = segment_data[:loaded_count]
        else:
            # Initialize with defaults
            self.segments = np.tile([3.0, 2.0, 0.85, 0.003], (self.num_segments, 1)).astype(np.float32)

    def add_segment(self, lookahead: float, lat_accel: float, margin: float, min_curv: float):
        """
        Add new segment to circular buffer (oldest replaced when full)

        Args:
            lookahead: Lookahead time in seconds
            lat_accel: Lateral acceleration limit in m/s²
            margin: Speed margin (0.0-1.0)
            min_curv: Minimum curvature threshold in rad/m
        """
        self.segments[self.segment_idx] = [lookahead, lat_accel, margin, min_curv]

        # Move to next position (circular)
        self.segment_idx = (self.segment_idx + 1) % self.num_segments

        # Increment valid count (saturates at num_segments)
        self.valid_segments = min(self.valid_segments + 1, self.num_segments)

    def get_parameters(self) -> tuple[dict, dict]:
        """
        Calculate median and std for all 4 parameters from valid segments

        Returns:
            Tuple of (mean_params, std_params) dicts

        Note:
            Uses median (not mean) for robustness to outliers.
        """
        if self.valid_segments == 0:
            # No data - return defaults
            return (
                {
                    'lookahead_time': 3.0,
                    'lat_accel_limit': 2.0,
                    'speed_margin': 0.85,
                    'min_curvature_threshold': 0.003
                },
                {
                    'lookahead_time_std': 0.0,
                    'lat_accel_limit_std': 0.0,
                    'speed_margin_std': 0.0,
                    'min_curvature_threshold_std': 0.0
                }
            )

        # Use only valid segments for statistics
        valid_data = self.segments[:self.valid_segments]

        # Calculate median (robust to outliers)
        mean_params = {
            'lookahead_time': float(np.median(valid_data[:, 0])),
            'lat_accel_limit': float(np.median(valid_data[:, 1])),
            'speed_margin': float(np.median(valid_data[:, 2])),
            'min_curvature_threshold': float(np.median(valid_data[:, 3]))
        }

        # Calculate std for confidence estimation
        std_params = {
            'lookahead_time_std': float(np.std(valid_data[:, 0])),
            'lat_accel_limit_std': float(np.std(valid_data[:, 1])),
            'speed_margin_std': float(np.std(valid_data[:, 2])),
            'min_curvature_threshold_std': float(np.std(valid_data[:, 3]))
        }

        return mean_params, std_params

    def get_buffer_data(self) -> np.ndarray:
        """
        Get segment data for serialization (only valid segments)

        Returns:
            Array of shape (valid_segments, 4) with raw segment data
        """
        return self.segments[:self.valid_segments].copy()

    def is_learned(self) -> bool:
        """
        Check if enough segments collected for confident learning

        Returns:
            True if valid_segments >= SEGMENTS_NEEDED, False otherwise
        """
        return self.valid_segments >= self.SEGMENTS_NEEDED


class Points:
    """
    Sliding window buffer for time-series data collection

    Used by delay learners and T_FOLLOW learner for collecting
    data points over a moving time window.

    Stores tuples of (time, desired, actual, okay) for each data point.
    """

    def __init__(self, num_points: int):
        """
        Initialize Points buffer

        Args:
            num_points: Maximum number of points to store (sliding window size)
        """
        self.times = deque[float]([0.0] * num_points, maxlen=num_points)
        self.okay = deque[bool]([False] * num_points, maxlen=num_points)
        self.desired = deque[float]([0.0] * num_points, maxlen=num_points)
        self.actual = deque[float]([0.0] * num_points, maxlen=num_points)

    @property
    def num_points(self):
        """Total number of points in buffer"""
        return len(self.desired)

    @property
    def num_okay(self):
        """Number of valid (okay=True) points in buffer"""
        return np.count_nonzero(self.okay)

    def update(self, t: float, desired: float, actual: float, okay: bool):
        """
        Add new data point to buffer (oldest removed when full)

        Args:
            t: Timestamp
            desired: Desired/expected value
            actual: Actual/measured value
            okay: True if data point is valid, False otherwise
        """
        self.times.append(t)
        self.okay.append(okay)
        self.desired.append(desired)
        self.actual.append(actual)

    def get(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """
        Get all data points as numpy arrays

        Returns:
            Tuple of (times, desired, actual, okay) arrays
        """
        return np.array(self.times), np.array(self.desired), np.array(self.actual), np.array(self.okay)


# Delay estimation constants
CORR_BORDER_OFFSET = 5
LAG_CANDIDATE_CORR_THRESHOLD = 0.9


def masked_normalized_cross_correlation(expected_sig: np.ndarray, actual_sig: np.ndarray, mask: np.ndarray, n: int):
    """
    Masked FFT-based normalized cross-correlation for delay estimation

    References:
        D. Padfield. "Masked FFT registration". In Proc. Computer Vision and
        Pattern Recognition, pp. 2918-2925 (2010).
        :DOI:`10.1109/CVPR.2010.5540032`
    """
    eps = np.finfo(np.float64).eps
    expected_sig = np.asarray(expected_sig, dtype=np.float64)
    actual_sig = np.asarray(actual_sig, dtype=np.float64)

    expected_sig[~mask] = 0.0
    actual_sig[~mask] = 0.0

    rotated_expected_sig = expected_sig[::-1]
    rotated_mask = mask[::-1]

    fft = partial(np.fft.fft, n=n)

    actual_sig_fft = fft(actual_sig)
    rotated_expected_sig_fft = fft(rotated_expected_sig)
    actual_mask_fft = fft(mask.astype(np.float64))
    rotated_mask_fft = fft(rotated_mask.astype(np.float64))

    number_overlap_masked_samples = np.fft.ifft(rotated_mask_fft * actual_mask_fft).real
    number_overlap_masked_samples[:] = np.round(number_overlap_masked_samples)
    number_overlap_masked_samples[:] = np.fmax(number_overlap_masked_samples, eps)
    masked_correlated_actual_fft = np.fft.ifft(rotated_mask_fft * actual_sig_fft).real
    masked_correlated_expected_fft = np.fft.ifft(actual_mask_fft * rotated_expected_sig_fft).real

    numerator = np.fft.ifft(rotated_expected_sig_fft * actual_sig_fft).real
    numerator -= masked_correlated_actual_fft * masked_correlated_expected_fft / number_overlap_masked_samples

    actual_squared_fft = fft(actual_sig ** 2)
    actual_sig_denom = np.fft.ifft(rotated_mask_fft * actual_squared_fft).real
    actual_sig_denom -= masked_correlated_actual_fft ** 2 / number_overlap_masked_samples
    actual_sig_denom[:] = np.fmax(actual_sig_denom, 0.0)

    rotated_expected_squared_fft = fft(rotated_expected_sig ** 2)
    expected_sig_denom = np.fft.ifft(actual_mask_fft * rotated_expected_squared_fft).real
    expected_sig_denom -= masked_correlated_expected_fft ** 2 / number_overlap_masked_samples
    expected_sig_denom[:] = np.fmax(expected_sig_denom, 0.0)

    denom = np.sqrt(actual_sig_denom * expected_sig_denom)

    # zero-out samples with very small denominators
    tol = 1e3 * eps * np.max(np.abs(denom), keepdims=True)
    nonzero_indices = denom > tol

    ncc = np.zeros_like(denom, dtype=np.float64)
    ncc[nonzero_indices] = numerator[nonzero_indices] / denom[nonzero_indices]
    np.clip(ncc, -1, 1, out=ncc)

    return ncc


def actuator_delay(expected_sig: np.ndarray, actual_sig: np.ndarray, mask: np.ndarray, dt: float, max_lag: float) -> tuple[float, float, float]:
    """
    Estimate actuator delay using cross-correlation

    Args:
        expected_sig: Expected/desired signal
        actual_sig: Actual/measured signal
        mask: Boolean mask for valid data points
        dt: Time step (seconds)
        max_lag: Maximum lag to consider (seconds)

    Returns:
        Tuple of (lag, correlation, confidence)
            lag: Estimated delay in seconds
            correlation: Maximum correlation coefficient (0-1)
            confidence: Confidence in estimate (0-1)
    """
    assert len(expected_sig) == len(actual_sig)
    max_lag_samples = int(max_lag / dt)
    padded_size = fft_next_good_size(len(expected_sig) + max_lag_samples)

    ncc = masked_normalized_cross_correlation(expected_sig, actual_sig, mask, padded_size)

    # only consider lags from 0 to max_lag
    roi = np.s_[len(expected_sig) - 1: len(expected_sig) - 1 + max_lag_samples]
    extended_roi = np.s_[roi.start - CORR_BORDER_OFFSET: roi.stop + CORR_BORDER_OFFSET]
    roi_ncc = ncc[roi]
    extended_roi_ncc = ncc[extended_roi]

    max_corr_index = np.argmax(roi_ncc)
    corr = roi_ncc[max_corr_index]
    lag = parabolic_peak_interp(roi_ncc, max_corr_index) * dt

    # to estimate lag confidence, gather all high-correlation candidates and see how spread they are
    # if e.g. 0.8 and 0.4 are both viable, this is an ambiguous case
    ncc_thresh = (roi_ncc.max() - roi_ncc.min()) * LAG_CANDIDATE_CORR_THRESHOLD + roi_ncc.min()
    good_lag_candidate_mask = extended_roi_ncc >= ncc_thresh
    good_lag_candidate_edges = np.diff(good_lag_candidate_mask.astype(int), prepend=0, append=0)
    starts, ends = np.where(good_lag_candidate_edges == 1)[0], np.where(good_lag_candidate_edges == -1)[0] - 1
    run_idx = np.searchsorted(starts, max_corr_index + CORR_BORDER_OFFSET, side='right') - 1
    width = ends[run_idx] - starts[run_idx] + 1
    confidence = np.clip(1 - width * dt, 0, 1)

    return lag, corr, confidence
