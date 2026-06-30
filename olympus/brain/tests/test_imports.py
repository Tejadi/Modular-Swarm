"""Smoke test: verify olympus_brain modules import without error.

Note: modules requiring `zenoh` (node, scout, executor, ai_agent) are
tested with explicit skip since the Zenoh Python lib may not be
installed on all dev machines.
"""

import importlib
import pytest


def _can_import(module_name: str) -> bool:
    try:
        importlib.import_module(module_name)
        return True
    except ImportError:
        return False


zenoh_available = _can_import("zenoh")
shapely_available = _can_import("shapely")


def test_protocol():
    from olympus_brain.protocol import (
        CommandType,
        DroneRole,
        DroneStatus,
        DetectionType,
        TaskType,
        EscalationLevel,
        ZenohKeys,
        Detection,
        Task,
    )
    # Verify new command types exist
    assert CommandType.RECALL_FOR_UPDATE == "recall_for_update"
    assert CommandType.REDEPLOY == "redeploy"
    assert CommandType.UPDATE_MODEL == "update_model"
    assert CommandType.MODEL_ACK == "model_ack"


def test_swarmnet():
    from olympus_brain.swarmnet import LocalTrainer, CentralAggregator, DriftDetector

    trainer = LocalTrainer(["hostile_activity", "vehicle_detected", "person_detected"])
    assert trainer.num_classes == 3
    assert trainer.get_accuracy() == 0.0

    # Test get_intermediate_features returns expected size
    from olympus_brain.protocol import GeoPosition
    pos = GeoPosition(latitude=34.05, longitude=-118.24, altitude=0.0)
    features = trainer.get_intermediate_features(pos)
    expected_size = (32 + 3) * 4  # feature_dim + num_classes * float32
    assert len(features) == expected_size

    # Test receive_peer_features accepts correct size
    trainer.receive_peer_features(features)  # Should not raise


def test_swarmnet_gps_denied():
    """Test GPS-denied relative feature encoding produces same-size features."""
    from olympus_brain.swarmnet import LocalTrainer
    from olympus_brain.protocol import GeoPosition

    classes = ["hostile_activity", "vehicle_detected", "person_detected"]
    trainer = LocalTrainer(classes, gps_denied=True)

    pos = GeoPosition(latitude=34.05, longitude=-118.24, altitude=30.0)
    features = trainer.get_intermediate_features(pos)
    expected_size = (32 + 3) * 4
    assert len(features) == expected_size

    # Verify relative encoding: move 100m east, features should change
    pos2 = GeoPosition(latitude=34.05, longitude=-118.239, altitude=30.0)
    features2 = trainer.get_intermediate_features(pos2)
    assert len(features2) == expected_size
    assert features != features2  # Different position → different features


def test_swarmnet_gps_mode_switch():
    """Test runtime switching between GPS and GPS-denied modes."""
    from olympus_brain.swarmnet import LocalTrainer
    from olympus_brain.protocol import GeoPosition

    classes = ["hostile_activity", "vehicle_detected"]
    trainer = LocalTrainer(classes)

    pos = GeoPosition(latitude=34.05, longitude=-118.24, altitude=30.0)
    assert not trainer._gps_denied

    # Extract features in GPS mode
    feat_gps = trainer.get_intermediate_features(pos)

    # Switch to GPS-denied
    trainer.set_gps_denied(True)
    assert trainer._gps_denied
    feat_denied = trainer.get_intermediate_features(pos)

    # Both should be same total size but different values
    assert len(feat_gps) == len(feat_denied)

    # Switch back
    trainer.set_gps_denied(False)
    assert not trainer._gps_denied


def test_drift_detector_downward():
    """DriftDetector should fire on accuracy DROPS (not just upward shifts)."""
    from olympus_brain.swarmnet import DriftDetector

    dd = DriftDetector(threshold=0.10, min_samples=10)

    # Feed stable high accuracy to establish baseline
    for _ in range(20):
        assert not dd.update(0.9)

    # Now feed a sharp accuracy drop — should trigger drift
    triggered = False
    for _ in range(30):
        if dd.update(0.3):
            triggered = True
            break

    assert triggered, "DriftDetector should fire on accuracy drop from 0.9 to 0.3"


def test_drift_detector_stable():
    """DriftDetector should NOT fire on stable accuracy."""
    from olympus_brain.swarmnet import DriftDetector

    dd = DriftDetector(threshold=0.10, min_samples=10)
    for _ in range(100):
        assert not dd.update(0.85), "Should not fire on stable accuracy"


def test_drift_detector_reset():
    from olympus_brain.swarmnet import DriftDetector

    dd = DriftDetector(threshold=0.10, min_samples=10)
    for _ in range(20):
        dd.update(0.9)

    dd.reset()
    assert dd._n == 0
    assert dd._sum == 0.0
    assert dd._ph_down == 0.0
    assert dd._ph_up == 0.0


def test_drift_detector_warmup():
    """DriftDetector should not fire during warmup period."""
    from olympus_brain.swarmnet import DriftDetector

    dd = DriftDetector(threshold=0.10, min_samples=50)
    for _ in range(49):
        result = dd.update(0.5)
        assert not result, "Should not fire during warmup period"


def test_athena():
    from olympus_brain.athena import (
        ThreatClassifier,
        ROEEnforcer,
        TacticalSymbolGenerator,
        ThreatLevel,
        ROEConfig,
    )
    assert ThreatLevel.GREEN == "green"
    assert ThreatLevel.AMBER == "amber"
    assert ThreatLevel.RED == "red"

    classifier = ThreatClassifier()
    enforcer = ROEEnforcer()
    symbols = TacticalSymbolGenerator()

    # Test classification
    from olympus_brain.protocol import Detection, DetectionType, GeoPosition
    det = Detection.create(
        detection_type=DetectionType.IED_SUSPECTED,
        position=GeoPosition(latitude=34.05, longitude=-118.24, altitude=0.0),
        confidence=0.95,
        detected_by="scout-01",
        severity=9,
    )
    assessment = classifier.classify(det)
    assert assessment.level == ThreatLevel.RED
    assert assessment.min_approach_distance_m == 50.0

    # Test symbol generation
    sym = symbols.get_symbol(det)
    assert sym["color"] == "#FF0000"
    assert sym["label"] == "IED"


def test_athena_roe_enforcement():
    """ROE should block IED investigation tasks."""
    from olympus_brain.athena import ROEEnforcer, ThreatClassifier, ThreatLevel
    from olympus_brain.protocol import (
        Detection, DetectionType, GeoPosition, Task, TaskType, TaskState,
    )

    classifier = ThreatClassifier()
    enforcer = ROEEnforcer()

    det = Detection.create(
        detection_type=DetectionType.IED_SUSPECTED,
        position=GeoPosition(latitude=34.05, longitude=-118.24, altitude=0.0),
        confidence=0.95,
        detected_by="scout-01",
        severity=9,
    )
    threat = classifier.classify(det)

    # IED + INVESTIGATE should be blocked
    task = Task.from_detection(det, TaskType.INVESTIGATE)
    allowed, reason = enforcer.validate_task(task, threat, available_drones=3)
    assert not allowed
    assert "IED" in reason or "ROE" in reason

    # IED + MARK should be allowed (but still blocked by RED approval rule)
    task_mark = Task.from_detection(det, TaskType.MARK)
    allowed_mark, reason_mark = enforcer.validate_task(task_mark, threat, available_drones=3)
    # RED requires approval so this is also blocked
    assert not allowed_mark
    assert "RED" in reason_mark or "ROE" in reason_mark


def test_athena_roe_buddy_system():
    """INVESTIGATE tasks should require minimum 2 drones (buddy system)."""
    from olympus_brain.athena import ROEEnforcer, ThreatClassifier, ROEConfig
    from olympus_brain.protocol import (
        Detection, DetectionType, GeoPosition, Task, TaskType,
    )

    # Use config that doesn't require RED approval so we can test buddy system
    config = ROEConfig(red_requires_approval=False)
    enforcer = ROEEnforcer(config=config)
    classifier = ThreatClassifier()

    det = Detection.create(
        detection_type=DetectionType.VEHICLE_DETECTED,
        position=GeoPosition(latitude=34.05, longitude=-118.24, altitude=0.0),
        confidence=0.80,
        detected_by="scout-01",
        severity=5,
    )
    threat = classifier.classify(det)
    task = Task.from_detection(det, TaskType.INVESTIGATE)

    # Only 1 drone available — should be blocked
    allowed, reason = enforcer.validate_task(task, threat, available_drones=1)
    assert not allowed
    assert "buddy" in reason.lower() or "min" in reason.lower()

    # 2 drones available — should pass
    allowed2, reason2 = enforcer.validate_task(task, threat, available_drones=2)
    assert allowed2


def test_detection_buffer():
    from olympus_brain.detection_buffer import DetectionBuffer
    from olympus_brain.protocol import Detection, DetectionType, GeoPosition
    import os

    db_path = "/tmp/test_olympus_smoke_buf.db"
    buf = DetectionBuffer(db_path=db_path)
    assert buf.total_count == 0
    assert buf.type_distribution() == {}

    # Store a detection
    det = Detection.create(
        detection_type=DetectionType.HOSTILE_ACTIVITY,
        position=GeoPosition(latitude=34.05, longitude=-118.24, altitude=0.0),
        confidence=0.85,
        detected_by="scout-01",
        severity=7,
    )
    buf.buffer(det)
    assert buf.total_count == 1
    dist = buf.type_distribution()
    assert dist.get("hostile_activity") == 1

    stats = buf.confidence_stats()
    assert stats["count"] == 1
    assert abs(stats["mean"] - 0.85) < 0.01

    # Query
    results = buf.query(detection_type="hostile_activity")
    assert len(results) == 1

    buf.close()
    os.unlink(db_path)


def test_detection_buffer_synced_first_eviction():
    """Ring buffer should evict synced records before unsynced ones."""
    from olympus_brain.detection_buffer import DetectionBuffer
    from olympus_brain.protocol import Detection, DetectionType, GeoPosition
    import os

    db_path = "/tmp/test_olympus_eviction.db"
    # Small buffer for testing eviction
    buf = DetectionBuffer(db_path=db_path, max_size=5)

    # Insert 5 detections and mark 3 as synced
    dets = []
    for i in range(5):
        det = Detection.create(
            detection_type=DetectionType.HOSTILE_ACTIVITY,
            position=GeoPosition(latitude=34.05 + i * 0.001, longitude=-118.24, altitude=0.0),
            confidence=0.80 + i * 0.01,
            detected_by="scout-01",
            severity=5,
        )
        buf.buffer(det)
        dets.append(det)

    assert buf.total_count == 5

    # Mark first 3 as synced
    buf.mark_synced([dets[0].id, dets[1].id, dets[2].id])

    # Verify pending count
    assert buf.pending_count == 2

    # Insert 3 more — should evict synced records first
    for i in range(3):
        det = Detection.create(
            detection_type=DetectionType.VEHICLE_DETECTED,
            position=GeoPosition(latitude=34.06, longitude=-118.24, altitude=0.0),
            confidence=0.90,
            detected_by="scout-02",
            severity=6,
        )
        buf.buffer(det)

    # Buffer should still be at max_size (5)
    assert buf.total_count == 5

    # The 2 original unsynced records should still be present
    # (synced ones were evicted first)
    unsynced = buf.get_unsynced(limit=10)
    # At least the 2 original unsynced + 3 new (all unsynced) = 5
    # But max_size is 5 so all 5 should be unsynced
    assert len(unsynced) == 5

    buf.close()
    os.unlink(db_path)


def test_mission_profile_default_athena():
    import os
    # Clear any existing override
    old = os.environ.pop("OLYMPUS_INSTANCE", None)
    try:
        # Force reimport
        import olympus_brain.mission_profile as mp
        importlib.reload(mp)
        profile = mp.load_profile()
        assert profile.id == "athena", f"Expected default athena, got {profile.id}"
        # Test task_to_detection reverse mapping exists
        assert hasattr(profile, "task_to_detection")
        t2d = profile.task_to_detection
        assert isinstance(t2d, dict)
        assert len(t2d) > 0
    finally:
        if old is not None:
            os.environ["OLYMPUS_INSTANCE"] = old


def test_escalation():
    from olympus_brain.escalation import EscalationEngine


@pytest.mark.skipif(not zenoh_available, reason="zenoh not installed")
def test_node_import():
    from olympus_brain.node import OlympusNode, NodeConfig


@pytest.mark.skipif(not zenoh_available or not shapely_available, reason="zenoh/shapely not installed")
def test_scout_import():
    from olympus_brain.scout import ScoutDrone, ScoutConfig


@pytest.mark.skipif(not zenoh_available, reason="zenoh not installed")
def test_ai_agent_import():
    from olympus_brain.ai_agent import AIAgent


def test_cli_register():
    from olympus_brain.cli_register import main
    assert callable(main)


def test_trust_tier_types():
    from olympus_brain.protocol import (
        TrustTier,
        CommandAuthority,
        RegistrationStatus,
        CapabilityManifest,
    )
    assert TrustTier.TRUSTED == "trusted"
    assert TrustTier.PARTNER == "partner"
    assert TrustTier.OBSERVER == "observer"
    assert CommandAuthority.BINDING == "binding"
    assert CommandAuthority.ADVISORY == "advisory"
    assert CommandAuthority.NONE == "none"
    assert RegistrationStatus.PENDING == "pending"
    assert RegistrationStatus.APPROVED == "approved"
    assert RegistrationStatus.REJECTED == "rejected"
    assert RegistrationStatus.REVOKED == "revoked"


def test_capability_manifest_defaults():
    from olympus_brain.protocol import CapabilityManifest, CommandAuthority

    m = CapabilityManifest()
    assert m.provides_telemetry is True
    assert m.provides_detections is False
    assert m.provides_features is False
    assert m.accepted_commands == []
    assert m.command_authority == CommandAuthority.NONE
    assert m.participates_in_cbba is False
    assert m.ttl_seconds == 3600
    assert m.data_encryption_required is False


def test_capability_manifest_partner():
    from olympus_brain.protocol import CapabilityManifest, CommandAuthority

    m = CapabilityManifest(
        provides_telemetry=True,
        provides_detections=True,
        accepted_commands=["emergency_stop", "recall_for_update"],
        command_authority=CommandAuthority.ADVISORY,
        participates_in_cbba=True,
        ttl_seconds=7200,
    )
    assert m.provides_detections is True
    assert len(m.accepted_commands) == 2
    assert "emergency_stop" in m.accepted_commands
    assert m.command_authority == CommandAuthority.ADVISORY
    assert m.participates_in_cbba is True
    assert m.ttl_seconds == 7200


def test_zenoh_keys_registry():
    from olympus_brain.protocol import ZenohKeys

    assert ZenohKeys.registry("partner-01") == "olympus/registry/partner-01"
    assert ZenohKeys.registry_wildcard() == "olympus/registry/*"


@pytest.mark.skipif(not zenoh_available, reason="zenoh not installed")
def test_swarm_state_trust_tracking():
    """SwarmState trust registry should filter commands by tier."""
    from olympus_brain.node import SwarmState, VehicleTrust
    from olympus_brain.protocol import (
        TrustTier, RegistrationStatus, CapabilityManifest,
        CommandAuthority, CommandType,
    )

    state = SwarmState()

    # Register a trusted vehicle
    state.register_vehicle("scout-01", TrustTier.TRUSTED, RegistrationStatus.APPROVED)
    assert state.get_trust_tier("scout-01") == TrustTier.TRUSTED
    assert state.is_command_allowed("scout-01", CommandType.RECALL_FOR_UPDATE)

    # Register a partner with limited commands
    manifest = CapabilityManifest(
        accepted_commands=["emergency_stop"],
        command_authority=CommandAuthority.ADVISORY,
        participates_in_cbba=True,
    )
    state.register_vehicle(
        "partner-01", TrustTier.PARTNER, RegistrationStatus.APPROVED, manifest
    )
    assert state.get_trust_tier("partner-01") == TrustTier.PARTNER
    assert state.is_command_allowed("partner-01", CommandType.EMERGENCY_STOP)
    assert not state.is_command_allowed("partner-01", CommandType.RECALL_FOR_UPDATE)

    # Register an observer
    state.register_vehicle("observer-01", TrustTier.OBSERVER, RegistrationStatus.APPROVED)
    assert not state.is_command_allowed("observer-01", CommandType.RECALL_FOR_UPDATE)
    # EMERGENCY_STOP always goes through
    assert state.is_command_allowed("observer-01", CommandType.EMERGENCY_STOP)

    # Revoke a vehicle
    state.revoke_vehicle("partner-01")
    assert not state.is_command_allowed("partner-01", CommandType.EMERGENCY_STOP)

    # CBBA participants
    participants = state.get_cbba_participants()
    assert "scout-01" in participants
    assert "partner-01" not in participants  # revoked
    assert "observer-01" not in participants  # observer
