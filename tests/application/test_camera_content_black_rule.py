from security_diagnosis_harness.application.camera_diagnosis_rules import (
    CameraDiagnosisLabel,
    infer_camera_black_screen_label,
)
from security_diagnosis_harness.domain.evidence import (
    DiagnosisEvidence,
    EvidenceSource,
    EvidenceType,
    Reliability,
)


def _stream_evidence(*, content_black: bool, pull_status: str = "success"):
    return DiagnosisEvidence(
        diagnosis_id="diag-black",
        evidence_type=EvidenceType.DEVICE_STREAM,
        source=EvidenceSource.DEVICE_GATEWAY,
        summary="受控码流与内容分析事实",
        payload={
            "pull_status": pull_status,
            "extra": {"content_black": content_black},
        },
        reliability=Reliability.HIGH,
    )


def test_healthy_stream_with_black_content_has_distinct_candidate() -> None:
    result = infer_camera_black_screen_label([_stream_evidence(content_black=True)])

    assert result.label is CameraDiagnosisLabel.VIDEO_CONTENT_BLACK_OR_OBSTRUCTED
    assert "视频内容持续纯黑=True" in result.evidence_chain


def test_black_candidate_requires_successful_stream() -> None:
    result = infer_camera_black_screen_label(
        [_stream_evidence(content_black=True, pull_status="failed")]
    )

    assert result.label is CameraDiagnosisLabel.STREAM_PUBLISH_OR_ENCODER_ISSUE


def test_healthy_non_black_stream_does_not_invent_black_candidate() -> None:
    result = infer_camera_black_screen_label([_stream_evidence(content_black=False)])

    assert result.label is CameraDiagnosisLabel.INSUFFICIENT_CAMERA_FACTS
