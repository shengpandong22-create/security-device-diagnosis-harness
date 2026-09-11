"""Phase 6A 收尾：统一脱敏的敏感键名规则验收。

重点验证安防标识键名（card_no / person_id / license_plate / pin ...）
既被正确脱敏，又不会因为过宽的子串匹配误伤普通业务键。
"""

from __future__ import annotations

import pytest

from security_diagnosis_harness.domain.device import REDACTED_VALUE
from security_diagnosis_harness.domain.redaction import (
    is_sensitive_key,
    redact_mapping,
    redact_text,
)


@pytest.mark.parametrize(
    "key",
    [
        "password",
        "admin_password",
        "passwd",
        "pwd",
        "token",
        "access_token",
        "refresh_token",
        "secret",
        "client_secret",
        "credential",
        "access_key",
        "accessKey",
        "private_key",
        "api_key",
    ],
)
def test_credential_keys_are_sensitive(key: str):
    assert is_sensitive_key(key) is True


@pytest.mark.parametrize(
    "key",
    [
        "card_no",
        "card_number",
        "card_id",
        "cardNo",
        "person_id",
        "id_card",
        "face_id",
        "face_feature",
        "face_template",
        "fingerprint",
        "finger_template",
        "license_plate",
        "phone",
        "mobile",
        "pin",
        "door_pin",
        "device_pin",
    ],
)
def test_security_identifier_keys_are_sensitive(key: str):
    assert is_sensitive_key(key) is True


@pytest.mark.parametrize(
    "key",
    [
        "spindle_speed",
        "pinned",
        "pinning",
        "spinner",
        "encoding",
        "resolution",
        "bitrate_kbps",
        "frame_rate",
        "channel_id",
        "device_id",
    ],
)
def test_ordinary_business_keys_are_not_sensitive(key: str):
    assert is_sensitive_key(key) is False


def test_nested_evidence_payload_card_no_is_redacted():
    payload, changed = redact_mapping({"nested": {"card_no": "330100001"}, "encoding": "H264"})

    assert changed is True
    assert payload["nested"]["card_no"] == REDACTED_VALUE
    assert payload["encoding"] == "H264"


def test_person_id_and_license_plate_are_redacted():
    payload, changed = redact_mapping(
        {"person_id": "person-88", "license_plate": "浙A12345", "channel_id": 1}
    )

    assert changed is True
    assert payload["person_id"] == REDACTED_VALUE
    assert payload["license_plate"] == REDACTED_VALUE
    assert payload["channel_id"] == 1


def test_spindle_speed_is_not_redacted():
    payload, changed = redact_mapping({"spindle_speed": 1200})

    assert changed is False
    assert payload["spindle_speed"] == 1200


def test_pin_sentence_is_not_swallowed():
    """"设备 PIN 配置异常"是自然语言，不应整句消失（无内联赋值）。"""
    text = "设备 PIN 配置异常，请检查硬件"
    cleaned, changed = redact_text(text)

    assert changed is False
    assert cleaned == text


def test_inline_pin_assignment_is_redacted():
    cleaned, changed = redact_text("pin=4821")

    assert changed is True
    assert "4821" not in cleaned
    assert REDACTED_VALUE in cleaned


def test_token_compatibility_is_unchanged():
    """既有 token 子串语义保持不变。"""
    assert is_sensitive_key("refresh_token") is True
    payload, changed = redact_mapping({"refresh_token": "abc"})
    assert changed is True
    assert payload["refresh_token"] == REDACTED_VALUE
