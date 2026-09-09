"""摄像头黑屏候选根因规则验收。"""

from __future__ import annotations

import pytest

from security_diagnosis_harness.application.camera_diagnosis_rules import (
    HIGH_BITRATE_KBPS,
    CameraDiagnosisLabel,
    _resolution_pixels,
    extract_camera_facts,
    infer_camera_black_screen_label,
    is_high_encoding_load,
)
from security_diagnosis_harness.domain.camera import (
    ChannelSnapshot,
    ChannelStatus,
    PlatformPullStatus,
    PullStatus,
    StreamKind,
    StreamSnapshot,
)
from security_diagnosis_harness.domain.evidence import (
    DiagnosisEvidence,
    EvidenceSource,
    EvidenceType,
)

DIAGNOSIS_ID = "diag_rules"


def _evidence(evidence_type: EvidenceType, payload: dict, summary: str = "证据"):
    return DiagnosisEvidence(
        diagnosis_id=DIAGNOSIS_ID,
        evidence_type=evidence_type,
        source=EvidenceSource.DEVICE_GATEWAY,
        summary=summary,
        payload=payload,
    )


def status_evidence(online: bool, stream_status: str = "normal") -> DiagnosisEvidence:
    return _evidence(
        EvidenceType.DEVICE_STATUS,
        {"online": online, "stream_status": stream_status},
    )


def channel_evidence(
    channel_status: str = "online",
    *,
    bound: bool = True,
    platform_registered: bool = True,
) -> DiagnosisEvidence:
    return _evidence(
        EvidenceType.DEVICE_CHANNEL,
        ChannelSnapshot(
            device_id="cam-x",
            channel_status=ChannelStatus(channel_status),
            bound=bound,
            platform_registered=platform_registered,
        ).model_dump(mode="json"),
    )


def stream_evidence(
    pull_status: str = "success",
    *,
    bitrate_kbps: int | None = 2048,
    resolution: str = "1920x1080",
    error_code: str | None = None,
) -> DiagnosisEvidence:
    return _evidence(
        EvidenceType.DEVICE_STREAM,
        StreamSnapshot(
            device_id="cam-x",
            stream_kind=StreamKind.MAIN,
            pull_status=PullStatus(pull_status),
            bitrate_kbps=bitrate_kbps,
            resolution=resolution,
            error_code=error_code,
        ).model_dump(mode="json"),
    )


def platform_evidence(pull_status: str = "success", error_code: str | None = None):
    return _evidence(
        EvidenceType.PLATFORM_PULL,
        PlatformPullStatus(
            device_id="cam-x",
            pull_status=PullStatus(pull_status),
            error_code=error_code,
        ).model_dump(mode="json"),
    )


def alarm_evidence(*event_types: str) -> DiagnosisEvidence:
    return _evidence(
        EvidenceType.DEVICE_ALARM,
        {"events": [{"event_type": event_type} for event_type in event_types]},
    )


def test_camera_offline_label():
    result = infer_camera_black_screen_label(
        [status_evidence(online=False, stream_status="unknown"), stream_evidence("failed")]
    )

    assert result.label is CameraDiagnosisLabel.DEVICE_OFFLINE_OR_NETWORK_UNREACHABLE
    assert result.evidence_chain
    assert result.troubleshooting_order
    assert result.excluded_candidates


def test_channel_offline_label():
    result = infer_camera_black_screen_label(
        [status_evidence(online=True), channel_evidence("offline")]
    )

    assert result.label is CameraDiagnosisLabel.CHANNEL_BINDING_OR_PLATFORM_ACCESS_ISSUE


def test_channel_online_but_not_registered_label():
    result = infer_camera_black_screen_label(
        [
            status_evidence(online=True),
            channel_evidence("online", platform_registered=False),
        ]
    )

    assert result.label is CameraDiagnosisLabel.CHANNEL_BINDING_OR_PLATFORM_ACCESS_ISSUE


def test_stream_publish_failed_label():
    result = infer_camera_black_screen_label(
        [
            status_evidence(online=True, stream_status="abnormal"),
            channel_evidence("online"),
            stream_evidence("failed", error_code="STREAM_PUBLISH_FAILED"),
        ]
    )

    assert result.label is CameraDiagnosisLabel.STREAM_PUBLISH_OR_ENCODER_ISSUE


def test_high_bitrate_encoder_timeout_label():
    result = infer_camera_black_screen_label(
        [
            status_evidence(online=True, stream_status="abnormal"),
            channel_evidence("online"),
            stream_evidence("timeout", bitrate_kbps=8192, resolution="2560x1440",
                            error_code="ENCODER_TIMEOUT"),
            alarm_evidence("ENCODER_TIMEOUT"),
        ]
    )

    assert result.label is CameraDiagnosisLabel.OVERLOADED_ENCODING_CONFIGURATION
    assert "R3" in result.matched_rule


def test_high_resolution_without_high_bitrate_still_overloaded():
    result = infer_camera_black_screen_label(
        [
            status_evidence(online=True),
            channel_evidence("online"),
            stream_evidence("timeout", bitrate_kbps=2048, resolution="3840x2160",
                            error_code="ENCODER_TIMEOUT"),
            alarm_evidence("ENCODER_TIMEOUT"),
        ]
    )

    assert result.label is CameraDiagnosisLabel.OVERLOADED_ENCODING_CONFIGURATION


def test_encoder_timeout_without_high_load_is_stream_issue():
    result = infer_camera_black_screen_label(
        [
            status_evidence(online=True, stream_status="abnormal"),
            channel_evidence("online"),
            stream_evidence("failed", bitrate_kbps=2048, resolution="1920x1080",
                            error_code="ENCODER_TIMEOUT"),
            alarm_evidence("ENCODER_TIMEOUT"),
        ]
    )

    assert result.label is CameraDiagnosisLabel.STREAM_PUBLISH_OR_ENCODER_ISSUE


def test_platform_pull_failed_label():
    result = infer_camera_black_screen_label(
        [
            status_evidence(online=True, stream_status="normal"),
            channel_evidence("online"),
            stream_evidence("success"),
            platform_evidence("failed", error_code="PLATFORM_PULL_FAILED"),
        ]
    )

    assert result.label is CameraDiagnosisLabel.PLATFORM_PULL_OR_ACCESS_PATH_ISSUE


def test_platform_pull_timeout_label():
    result = infer_camera_black_screen_label(
        [
            status_evidence(online=True),
            channel_evidence("online"),
            stream_evidence("success"),
            platform_evidence("timeout"),
        ]
    )

    assert result.label is CameraDiagnosisLabel.PLATFORM_PULL_OR_ACCESS_PATH_ISSUE


def test_insufficient_facts_label_when_no_evidence():
    result = infer_camera_black_screen_label([])

    assert result.label is CameraDiagnosisLabel.INSUFFICIENT_CAMERA_FACTS
    assert result.matched_rule.startswith("R0")


def test_insufficient_facts_label_when_only_knowledge():
    result = infer_camera_black_screen_label(
        [_evidence(EvidenceType.KNOWLEDGE_SOP, {"sops": []})]
    )

    assert result.label is CameraDiagnosisLabel.INSUFFICIENT_CAMERA_FACTS


def test_insufficient_facts_label_when_everything_normal():
    result = infer_camera_black_screen_label(
        [
            status_evidence(online=True),
            channel_evidence("online"),
            stream_evidence("success"),
            platform_evidence("success"),
        ]
    )

    assert result.label is CameraDiagnosisLabel.INSUFFICIENT_CAMERA_FACTS
    assert result.matched_rule.startswith("R6")


def test_every_label_has_explanation_and_troubleshooting_order():
    from security_diagnosis_harness.application.camera_diagnosis_rules import (
        LABEL_EXPLANATIONS,
        TROUBLESHOOTING_ORDER,
    )

    for label in CameraDiagnosisLabel:
        assert LABEL_EXPLANATIONS[label]
        assert TROUBLESHOOTING_ORDER[label]

    result = infer_camera_black_screen_label([status_evidence(online=False)])
    assert result.explanation
    assert result.troubleshooting_order


def test_rules_never_return_confirmed():
    results = [
        infer_camera_black_screen_label([status_evidence(online=False)]),
        infer_camera_black_screen_label([]),
    ]

    for result in results:
        assert result.label.value != "confirmed"
        assert "confirmed" not in result.label.value


def test_extract_camera_facts_collects_all_dimensions():
    facts = extract_camera_facts(
        [
            status_evidence(online=True, stream_status="abnormal"),
            channel_evidence("offline", platform_registered=False),
            stream_evidence("failed", bitrate_kbps=4096, resolution="1920x1080",
                            error_code="E1"),
            platform_evidence("timeout", error_code="P1"),
            alarm_evidence("ENCODER_TIMEOUT", "HIGH_CPU_USAGE"),
        ]
    )

    assert facts.has_device_status is True
    assert facts.has_channel is True
    assert facts.has_stream is True
    assert facts.has_platform_pull is True
    assert facts.online is True
    assert facts.channel_status == "offline"
    assert facts.platform_registered is False
    assert facts.stream_pull_status == "failed"
    assert facts.platform_pull_status == "timeout"
    assert facts.bitrate_kbps == 4096
    assert facts.resolution == "1920x1080"
    assert facts.alarm_types == ["ENCODER_TIMEOUT", "HIGH_CPU_USAGE"]
    assert len(facts.device_fact_types) >= 4
    assert facts.has_any_camera_fact is True


def test_extract_camera_facts_uses_config_as_fallback():
    facts = extract_camera_facts(
        [
            _evidence(
                EvidenceType.DEVICE_CONFIG,
                {"bitrate_kbps": 8192, "resolution": "2560x1440"},
            )
        ]
    )

    assert facts.bitrate_kbps == 8192
    assert facts.resolution == "2560x1440"


@pytest.mark.parametrize(
    ("bitrate", "resolution", "expected"),
    [
        (8192, "1920x1080", True),
        (2048, "2560x1440", True),
        (2048, "1920x1080", False),
        (None, None, False),
        (HIGH_BITRATE_KBPS, "640x480", True),
    ],
)
def test_is_high_encoding_load(bitrate, resolution, expected):
    assert is_high_encoding_load(bitrate, resolution) is expected


@pytest.mark.parametrize(
    ("resolution", "expected"),
    [
        ("1920x1080", 1920 * 1080),
        ("2560x1440", 2560 * 1440),
        ("", 0),
        (None, 0),
        ("not-a-resolution", 0),
    ],
)
def test_resolution_pixels(resolution, expected):
    assert _resolution_pixels(resolution) == expected
