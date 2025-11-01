#!/usr/bin/env python3
"""
Learner Orchestrator (lagd.py)

Coordinates multiple learning modules:
- LateralLagEstimator: Learn steering actuator delay
- LongitudinalLagEstimator: Learn acceleration actuator delay
- PersonalizedLongitudinalLearner: Learn driver's preferred T_FOLLOW scales
- CurveSpeedLearner: Learn driver's preferred curve speed parameters

Responsibilities:
1. Subscribe to cereal messages
2. Route messages to appropriate learners
3. Coordinate learner updates (20Hz data collection, 4Hz estimation)
4. Aggregate learner data into liveDelay message
5. Persist learned parameters to Params storage (every 60s)
6. Load learned parameters on startup
"""

import os
import numpy as np

import cereal.messaging as messaging
from cereal import car, log
from cereal.services import SERVICE_LIST
from openpilot.common.params import Params
from openpilot.common.realtime import config_realtime_process
from openpilot.common.swaglog import cloudlog

# Import all learner modules
from selfdrive.locationd.learners.lateral_delay import LateralLagEstimator
from selfdrive.locationd.learners.long_delay import LongitudinalLagEstimator
from selfdrive.locationd.learners.t_follow import PersonalizedLongitudinalLearner
from selfdrive.locationd.learners.curve_speed import CurveSpeedLearner

# Legacy constants (for backward compatibility with retrieve_initial_lag)
BLOCK_NUM = 50
BLOCK_NUM_NEEDED = 5


def retrieve_initial_lag(params: Params, CP: car.CarParams):
    """Retrieve learned parameters from persistent storage"""
    last_lag_data = params.get("LiveDelay")
    last_carparams_data = params.get("CarParamsPrevRoute")

    if last_lag_data is not None:
        try:
            with log.Event.from_bytes(last_lag_data) as last_lag_msg, car.CarParams.from_bytes(last_carparams_data) as last_CP:
                ld = last_lag_msg.liveDelay
                if last_CP.carFingerprint != CP.carFingerprint:
                    raise Exception("Car model mismatch")

                # Lateral delay
                lateral_lag = ld.lateralDelayEstimate
                lateral_valid_blocks = ld.validBlocks
                lateral_status = ld.status
                assert lateral_valid_blocks <= BLOCK_NUM, "Invalid number of lateral valid blocks"
                assert lateral_status != log.LiveDelayData.Status.invalid, "Lateral lag estimate is invalid"

                # Longitudinal delay
                longitudinal_lag = ld.longitudinalDelayEstimate
                longitudinal_valid_blocks = ld.longitudinalValidBlocks
                longitudinal_status = ld.longitudinalStatus

                # Personalized T_FOLLOW scales
                if hasattr(ld, 'personalizedScales') and len(ld.personalizedScales) > 0:
                    personalized_scales = list(ld.personalizedScales)
                    personalized_valid_blocks = list(ld.personalizedValidBlocks)
                    personalized_status = ld.personalizedStatus

                    # Load raw block data for proper std calculation
                    if hasattr(ld, 'personalizedBlockData') and len(ld.personalizedBlockData) > 0:
                        personalized_block_data = np.array(ld.personalizedBlockData, dtype=np.float32)
                    else:
                        personalized_block_data = None
                else:
                    personalized_scales = None
                    personalized_valid_blocks = None
                    personalized_status = None
                    personalized_block_data = None

                # Curve speed control - load segment buffer data
                if hasattr(ld, 'curveSpeedValidSegments') and ld.curveSpeedValidSegments > 0:
                    # Load raw segment buffer (50 segments × 4 parameters = 200 floats)
                    if hasattr(ld, 'curveSpeedSegmentBuffer') and len(ld.curveSpeedSegmentBuffer) > 0:
                        buffer_data = np.array(ld.curveSpeedSegmentBuffer, dtype=np.float32).reshape(-1, 4)
                        curve_speed_buffer = buffer_data
                    else:
                        curve_speed_buffer = None

                    curve_speed_valid_segments = ld.curveSpeedValidSegments
                    curve_speed_status = ld.curveSpeedStatus
                else:
                    curve_speed_buffer = None
                    curve_speed_valid_segments = None
                    curve_speed_status = None

                # Return dict with all learned parameters
                return {
                    'lateral': (lateral_lag, lateral_valid_blocks) if lateral_status == log.LiveDelayData.Status.estimated else None,
                    'longitudinal': (longitudinal_lag, longitudinal_valid_blocks) if longitudinal_status == log.LiveDelayData.Status.estimated else None,
                    'personalized': (personalized_scales, personalized_valid_blocks, personalized_block_data) if personalized_scales and personalized_status == log.LiveDelayData.PersonalizedStatus.learned else None,
                    'curve_speed': (curve_speed_buffer, curve_speed_valid_segments) if curve_speed_buffer is not None and curve_speed_status == log.LiveDelayData.PersonalizedStatus.learned else None,
                }
        except Exception as e:
            cloudlog.error(f"Failed to retrieve initial lag: {e}")
            params.remove("LiveDelay")

    return None


def apply_learned_longitudinal_delay(CP: car.CarParams, params: Params) -> car.CarParams:
    """
    Apply learned longitudinal delay to CarParams for improved MPC velocity extraction accuracy

    This function loads the learned longitudinal delay from persistent storage and updates
    CarParams.longitudinalActuatorDelay if the learned value meets confidence thresholds.

    Returns: Updated CarParams with learned delay, or original CP if no valid learned delay
    """
    initial_lag_data = retrieve_initial_lag(params, CP)

    if initial_lag_data is None or initial_lag_data.get('longitudinal') is None:
        cloudlog.info(f"No learned longitudinal delay available, using default: {CP.longitudinalActuatorDelay:.3f}s")
        return CP

    learned_delay, valid_blocks = initial_lag_data['longitudinal']

    # Sanity check: Learned delay must be reasonable (0.05s to 1.0s)
    if not (0.05 <= learned_delay <= 1.0):
        cloudlog.warning(f"Learned longitudinal delay {learned_delay:.3f}s outside valid range [0.05s, 1.0s], using default")
        return CP

    # Require minimum confidence (at least BLOCK_NUM_NEEDED valid blocks)
    if valid_blocks < BLOCK_NUM_NEEDED:
        cloudlog.info(f"Learned longitudinal delay confidence too low ({valid_blocks} blocks < {BLOCK_NUM_NEEDED} required), using default")
        return CP

    # Update CarParams with learned delay (CP is mutable)
    original_delay = CP.longitudinalActuatorDelay
    CP.longitudinalActuatorDelay = learned_delay

    cloudlog.info(f"Applied learned longitudinal delay: {original_delay:.3f}s → {learned_delay:.3f}s ({valid_blocks} valid blocks)")

    return CP


def main():
    config_realtime_process([0, 1, 2, 3], 5)

    DEBUG = bool(int(os.getenv("DEBUG", "0")))

    # Setup messaging
    pm = messaging.PubMaster(['liveDelay'])
    sm = messaging.SubMaster([
        'livePose', 'liveCalibration', 'carState', 'controlsState', 'carControl',
        'modelV2', 'longitudinalPlan', 'radarState'
    ], poll='livePose')

    params = Params()
    CP = messaging.log_from_bytes(params.get("CarParams", block=True), car.CarParams)

    # Initialize all learners
    lateral_learner = LateralLagEstimator(CP, 1. / SERVICE_LIST['livePose'].frequency)
    longitudinal_learner = LongitudinalLagEstimator(CP, 1. / 20.0)  # 20Hz for modelV2
    t_follow_learner = PersonalizedLongitudinalLearner(CP, 1. / 20.0)  # 20Hz for radarState/modelV2
    curve_speed_learner = CurveSpeedLearner(CP, 1. / 20.0)  # 20Hz for modelV2

    # Load learned parameters from persistent storage
    if (initial_lag_params := retrieve_initial_lag(params, CP)) is not None:
        if initial_lag_params.get('lateral') is not None:
            lateral_lag, lateral_valid_blocks = initial_lag_params['lateral']
            lateral_learner.reset(lateral_lag, lateral_valid_blocks)
            cloudlog.info(f"Loaded learned lateral delay: {lateral_lag:.3f}s ({lateral_valid_blocks} valid blocks)")

        if initial_lag_params.get('longitudinal') is not None:
            longitudinal_lag, longitudinal_valid_blocks = initial_lag_params['longitudinal']
            longitudinal_learner.reset(longitudinal_lag, longitudinal_valid_blocks)

        if initial_lag_params.get('personalized') is not None:
            personalized_scales, personalized_valid_blocks, personalized_block_data = initial_lag_params['personalized']
            t_follow_learner.reset(personalized_scales, personalized_valid_blocks, personalized_block_data)

        if initial_lag_params.get('curve_speed') is not None:
            curve_speed_params, curve_speed_valid_segments = initial_lag_params['curve_speed']
            curve_speed_learner.reset(curve_speed_params, curve_speed_valid_segments)

    # Main loop
    while True:
        sm.update()
        if sm.all_checks():
            # Route messages to learners
            for which in sorted(sm.updated.keys(), key=lambda x: sm.logMonoTime[x]):
                if sm.updated[which]:
                    t = sm.logMonoTime[which] * 1e-9

                    # Route to appropriate learners
                    if which in lateral_learner.inputs:
                        lateral_learner.handle_log(t, which, sm[which])
                    if which in longitudinal_learner.inputs:
                        longitudinal_learner.handle_log(t, which, sm[which])
                    if which in t_follow_learner.inputs:
                        t_follow_learner.handle_log(t, which, sm[which])
                    if which in curve_speed_learner.inputs:
                        curve_speed_learner.handle_log(t, which, sm[which])

            # Update learners (20Hz)
            lateral_learner.update(sm.logMonoTime['livePose'] * 1e-9)
            longitudinal_learner.update(sm.logMonoTime.get('modelV2', 0) * 1e-9)
            t_follow_learner.update(sm.logMonoTime.get('radarState', 0) * 1e-9)
            curve_speed_learner.update(sm.logMonoTime.get('modelV2', 0) * 1e-9)

        # Update estimates and publish (4Hz - driven by livePose)
        if sm.frame % 5 == 0:
            # Update estimates
            lateral_learner.update_estimate()
            longitudinal_learner.update_estimate()
            t_follow_learner.update_estimate()

            # Create liveDelay message
            msg = messaging.new_message('liveDelay')
            msg.valid = sm.all_checks()
            liveDelay = msg.liveDelay

            # Collect data from all learners
            lateral_data = lateral_learner.get_msg_data()
            longitudinal_data = longitudinal_learner.get_msg_data()
            personalized_data = t_follow_learner.get_msg_data()
            curve_speed_data = curve_speed_learner.get_msg_data()

            # Populate lateral delay fields
            liveDelay.lateralDelay = lateral_data['lateralDelay']
            liveDelay.lateralDelayEstimate = lateral_data['lateralDelayEstimate']
            liveDelay.lateralDelayEstimateStd = lateral_data['lateralDelayEstimateStd']
            liveDelay.validBlocks = lateral_data['validBlocks']
            liveDelay.status = lateral_data['status']
            liveDelay.calPerc = lateral_data['calPerc']
            if DEBUG:
                liveDelay.points = lateral_data['points']

            # Populate longitudinal delay fields
            liveDelay.longitudinalDelay = longitudinal_data['longitudinalDelay']
            liveDelay.longitudinalDelayEstimate = longitudinal_data['longitudinalDelayEstimate']
            liveDelay.longitudinalDelayEstimateStd = longitudinal_data['longitudinalDelayEstimateStd']
            liveDelay.longitudinalValidBlocks = longitudinal_data['longitudinalValidBlocks']
            liveDelay.longitudinalStatus = longitudinal_data['longitudinalStatus']
            liveDelay.longitudinalCalPerc = longitudinal_data['longitudinalCalPerc']
            liveDelay.visionSpeed = longitudinal_data['visionSpeed']
            liveDelay.canSpeed = longitudinal_data['canSpeed']
            liveDelay.visionCanDiff = longitudinal_data['visionCanDiff']
            liveDelay.visionCanSafetyPassed = longitudinal_data['visionCanSafetyPassed']
            if DEBUG:
                liveDelay.longitudinalPoints = longitudinal_data['longitudinalPoints']

            # Populate personalized T_FOLLOW fields
            liveDelay.personalizedScales = personalized_data['personalizedScales']
            liveDelay.personalizedValidBlocks = personalized_data['personalizedValidBlocks']
            liveDelay.personalizedProgress = personalized_data['personalizedProgress']
            liveDelay.personalizedStatus = personalized_data['personalizedStatus']
            liveDelay.personalizedActiveInterval = personalized_data['personalizedActiveInterval']

            # Serialize personalized block data for persistence
            t_follow_serialization = t_follow_learner.get_serialization_data()
            liveDelay.personalizedBlockData = t_follow_serialization['block_data'].tolist()

            # Populate curve speed control fields
            liveDelay.curveSpeedLookaheadTime = curve_speed_data['curveSpeedLookaheadTime']
            liveDelay.curveSpeedLatAccelLimit = curve_speed_data['curveSpeedLatAccelLimit']
            liveDelay.curveSpeedSpeedMargin = curve_speed_data['curveSpeedSpeedMargin']
            liveDelay.curveSpeedMinCurvatureThreshold = curve_speed_data['curveSpeedMinCurvatureThreshold']
            liveDelay.curveSpeedValidSegments = curve_speed_data['curveSpeedValidSegments']
            liveDelay.curveSpeedProgress = curve_speed_data['curveSpeedProgress']
            liveDelay.curveSpeedStatus = curve_speed_data['curveSpeedStatus']
            liveDelay.curveSpeedLookaheadTimeStd = curve_speed_data['curveSpeedLookaheadTimeStd']
            liveDelay.curveSpeedLatAccelLimitStd = curve_speed_data['curveSpeedLatAccelLimitStd']
            liveDelay.curveSpeedSpeedMarginStd = curve_speed_data['curveSpeedSpeedMarginStd']
            liveDelay.curveSpeedMinCurvatureThresholdStd = curve_speed_data['curveSpeedMinCurvatureThresholdStd']

            # Serialize curve speed segment buffer for persistence
            curve_speed_serialization = curve_speed_learner.get_serialization_data()
            liveDelay.curveSpeedSegmentBuffer = curve_speed_serialization['segment_buffer_data'].flatten().tolist()

            # Publish liveDelay message
            msg_dat = msg.to_bytes()
            pm.send('liveDelay', msg_dat)

            # Persist to Params every 60 seconds
            if sm.frame % 1200 == 0:
                params.put_nonblocking("LiveDelay", msg_dat)


if __name__ == "__main__":
    main()
