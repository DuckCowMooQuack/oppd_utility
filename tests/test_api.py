"""Unit tests for OPPD response normalization."""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from custom_components.oppd_utility.api import (
    OppdApi,
    OppdTokens,
    OppdUsageRecord,
    allocate_intervals_daily,
)


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.status = 200
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    async def json(self, *, content_type=None):
        return self._payload

    async def text(self) -> str:
        return ""


class _FakeSession:
    def __init__(self, *payloads: dict[str, Any]) -> None:
        self._payloads = list(payloads)
        self.requests: list[dict[str, Any]] = []

    def request(self, method, url, **kwargs):
        self.requests.append({"method": method, "url": url, **kwargs})
        return _FakeResponse(self._payloads.pop(0))


def test_parse_naive_oppd_datetime_as_central() -> None:
    parsed = OppdApi._parse_datetime("2026-07-01T00:00:00")
    assert parsed == datetime(2026, 7, 1, 5, tzinfo=timezone.utc)


def test_convert_supported_energy_units_to_kwh() -> None:
    assert OppdApi._to_kwh("1,250", "Wh") == 1.25
    assert OppdApi._to_kwh("1.25", "kWh") == 1.25
    assert OppdApi._to_kwh("1.25", "MWh") == 1250.0


def test_combine_same_interval_for_multiple_meters() -> None:
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    records = [
        OppdUsageRecord(start, None, 2.5, "one"),
        OppdUsageRecord(start, None, 3.5, "two"),
    ]
    combined = OppdApi._combine_intervals(records)
    assert len(combined) == 1
    assert combined[0].consumption_kwh == 6.0
    assert combined[0].meter_number is None


def test_app_headers_include_oppd_utility_id() -> None:
    api = object.__new__(OppdApi)
    api._tokens = OppdTokens("example", "refresh")
    api.user_uuid = "example-user"
    headers = api._headers(authenticated=True)
    assert headers["uid"] == "1"
    assert headers["uuid"] == "example-user"
    assert headers["Authorization"] == "Bearer example"


def test_allocate_monthly_interval_across_actual_days() -> None:
    start = datetime(2026, 4, 30, 23, tzinfo=timezone.utc)
    records = [
        OppdUsageRecord(
            start=start,
            end=start + timedelta(days=33),
            consumption_kwh=990.0,
            meter_number="one",
        )
    ]

    allocated = allocate_intervals_daily(records)

    assert len(allocated) == 33
    assert allocated[0].start == start
    assert allocated[-1].end == start + timedelta(days=33)
    assert allocated[0].consumption_kwh == pytest.approx(30.0)
    assert sum(record.consumption_kwh for record in allocated) == pytest.approx(990.0)


def test_daily_or_incomplete_interval_is_not_allocated() -> None:
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    daily = OppdUsageRecord(
        start=start,
        end=start + timedelta(days=1),
        consumption_kwh=12.5,
        meter_number=None,
    )
    incomplete = OppdUsageRecord(start, None, 20.0, None)

    assert allocate_intervals_daily([daily, incomplete]) == [daily, incomplete]


def test_login_parses_tokens_user_and_mfa_methods() -> None:
    session = _FakeSession(
        {
            "status": {"code": 200, "error": False},
            "data": {
                "accessToken": "access",
                "refreshToken": "refresh",
                "user": {"uuid": "user-id"},
                "tfaDetails": {
                    "phone": "***1234",
                    "email": "m***@example.com",
                    "isAuthenticatorAppEnabled": True,
                },
            },
        }
    )
    api = OppdApi.for_login(session)

    result = asyncio.run(api.async_login("someone", "secret"))

    assert result.user_uuid == "user-id"
    assert result.tokens == OppdTokens("access", "refresh")
    assert result.tfa_details is not None
    assert result.tfa_details.phone == "***1234"
    assert result.tfa_details.authenticator_enabled is True
    assert session.requests[0]["json"]["userName"] == "someone"
    assert session.requests[0]["json"]["customAttributes"]["deviceName"] == (
        "Home Assistant"
    )


def test_account_and_meter_discovery_parsing() -> None:
    session = _FakeSession(
        {
            "status": {"code": 200, "error": False},
            "data": [
                {
                    "accountNumber": "second",
                    "nickName": "Cabin",
                    "default": False,
                    "serviceAddress": {"city": "Omaha"},
                },
                {
                    "accountNumber": "first",
                    "isDefault": True,
                    "serviceAddress": {
                        "address1": "Main Street",
                        "premiseNumber": "premise",
                    },
                },
            ],
        },
        {
            "status": {"code": 200, "error": False},
            "data": [
                {
                    "meterNumber": "meter-2",
                    "meterSubType": "1",
                    "amiMeter": "true",
                },
                {"meterNumber": "meter-1", "amiMeter": 0},
            ],
        },
    )
    api = OppdApi(session, "access", "refresh", "user-id", "")

    accounts = asyncio.run(api.async_get_accounts())
    meters = asyncio.run(api.async_get_meters("first"))

    assert [account.account_number for account in accounts] == ["first", "second"]
    assert accounts[0].premise_number == "premise"
    assert [meter.meter_number for meter in meters] == ["meter-1", "meter-2"]
    assert meters[1].is_ami is True
