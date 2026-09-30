"""Async client for the MyOPPD API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from math import isclose
from typing import Any
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

from aiohttp import ClientResponse, ClientSession, ClientTimeout

from .const import (
    ACCOUNT_ADDRESS_URL,
    APP_VERSION,
    ELECTRIC_USAGE_URL,
    LOGIN_URL,
    METER_DISCOVERY_URL,
    PERIODICITY_DAILY,
    REFRESH_URL,
    SEND_TFA_URL,
    USER_AGENT,
    VERIFY_TFA_URL,
)

_TIMEOUT = ClientTimeout(total=30)
_OPPD_TIME_ZONE = ZoneInfo("America/Chicago")


class OppdApiError(Exception):
    """Base MyOPPD API error."""


class OppdAuthError(OppdApiError):
    """Raised when MyOPPD rejects authentication."""


class OppdResponseError(OppdApiError):
    """Raised when MyOPPD returns an unexpected response."""


class OppdNoDataError(OppdApiError):
    """Raised when MyOPPD has no usage for a requested range."""


@dataclass(frozen=True, slots=True)
class OppdTokens:
    """Rotating MyOPPD tokens."""

    access_token: str
    refresh_token: str


@dataclass(frozen=True, slots=True)
class OppdTfaDetails:
    """Available MyOPPD multi-factor authentication methods."""

    phone: str | None
    email: str | None
    authenticator_enabled: bool


@dataclass(frozen=True, slots=True)
class OppdLoginResult:
    """Result of the initial username/password request."""

    user_uuid: str
    tokens: OppdTokens | None
    tfa_details: OppdTfaDetails | None


@dataclass(frozen=True, slots=True)
class OppdAccount:
    """One account returned by MyOPPD account discovery."""

    account_number: str
    label: str
    premise_number: str | None
    is_default: bool


@dataclass(frozen=True, slots=True)
class OppdMeter:
    """One meter returned by MyOPPD meter discovery."""

    meter_number: str
    meter_type: str | None
    is_ami: bool


@dataclass(frozen=True, slots=True)
class OppdUsageRecord:
    """One normalized OPPD electricity-consumption interval."""

    start: datetime
    end: datetime | None
    consumption_kwh: float
    meter_number: str | None


def allocate_intervals_daily(
    records: list[OppdUsageRecord],
) -> list[OppdUsageRecord]:
    """Evenly allocate multi-day totals into daily estimated intervals.

    MyOPPD may only expose billing-period totals. Home Assistant groups external
    statistics by calendar time, so storing a whole bill at ``readingFrom`` can
    combine two bills in one month and leave the next month empty. Daily
    allocation preserves every billing total while making calendar aggregation
    useful. It does not imply that OPPD supplied daily meter readings.
    """
    allocated: list[OppdUsageRecord] = []
    seconds_per_day = timedelta(days=1).total_seconds()

    for record in records:
        if record.end is None or record.end <= record.start:
            allocated.append(record)
            continue

        duration_days = (record.end - record.start).total_seconds() / seconds_per_day
        day_count = round(duration_days)
        if day_count <= 1 or not isclose(duration_days, day_count, abs_tol=1 / 60):
            allocated.append(record)
            continue

        daily_consumption = record.consumption_kwh / day_count
        allocated_total = 0.0
        for day_index in range(day_count):
            start = record.start + timedelta(days=day_index)
            if day_index == day_count - 1:
                consumption = record.consumption_kwh - allocated_total
            else:
                consumption = daily_consumption
                allocated_total += consumption
            allocated.append(
                OppdUsageRecord(
                    start=start,
                    end=start + timedelta(days=1),
                    consumption_kwh=consumption,
                    meter_number=record.meter_number,
                )
            )

    return allocated


class OppdApi:
    """Client for MyOPPD token refresh and electric usage."""

    def __init__(
        self,
        session: ClientSession,
        access_token: str,
        refresh_token: str,
        user_uuid: str,
        account_number: str,
        meter_number: str = "",
    ) -> None:
        self._session = session
        self._tokens = OppdTokens(
            access_token=access_token.strip(),
            refresh_token=refresh_token.strip(),
        )
        self.user_uuid = user_uuid.strip()
        self.account_number = account_number.strip()
        self.meter_number = meter_number.strip()

    @classmethod
    def for_login(cls, session: ClientSession) -> OppdApi:
        """Create a client without an existing authenticated session."""
        return cls(session, "", "", "", "")

    @property
    def tokens(self) -> OppdTokens:
        """Return the current token pair."""
        return self._tokens

    async def async_login(self, username: str, password: str) -> OppdLoginResult:
        """Start a MyOPPD username/password login."""
        payload = await self._async_post_json(
            LOGIN_URL,
            {
                "userName": username.strip(),
                "password": password,
                "guestToken": "",
                "customAttributes": self._custom_attributes(),
            },
            authenticated=False,
        )
        data = self._data_object(payload)
        user = data.get("user")
        if not isinstance(user, dict):
            raise OppdResponseError("MyOPPD login did not return user details")

        user_uuid = self._clean_string(user.get("uuid"))
        if not user_uuid:
            raise OppdResponseError("MyOPPD login did not return a user UUID")
        self.user_uuid = user_uuid

        access_token = self._clean_string(data.get("accessToken"))
        refresh_token = self._clean_string(data.get("refreshToken"))
        tokens = None
        if access_token and refresh_token:
            tokens = OppdTokens(access_token, refresh_token)
            self._tokens = tokens

        tfa_details = None
        raw_tfa = data.get("tfaDetails")
        if isinstance(raw_tfa, dict):
            tfa_details = OppdTfaDetails(
                phone=self._optional_contact(raw_tfa.get("phone")),
                email=self._optional_contact(raw_tfa.get("email")),
                authenticator_enabled=raw_tfa.get("isAuthenticatorAppEnabled") is True,
            )

        if tokens is None and tfa_details is None:
            raise OppdResponseError(
                "MyOPPD login returned neither tokens nor an MFA challenge"
            )
        return OppdLoginResult(user_uuid, tokens, tfa_details)

    async def async_send_tfa(self, mode: int) -> None:
        """Send a MyOPPD MFA code by the selected mode."""
        await self._async_post_json(
            SEND_TFA_URL,
            {
                "uuid": self.user_uuid,
                "mode": mode,
                "customAttributes": self._custom_attributes(),
            },
            authenticated=True,
        )

    async def async_verify_tfa(self, mode: int, code: str) -> OppdTokens:
        """Verify a MyOPPD MFA code and return the authenticated token pair."""
        payload = await self._async_post_json(
            VERIFY_TFA_URL,
            {
                "uuid": self.user_uuid,
                "mode": mode,
                "code": code.strip(),
                "customAttributes": self._custom_attributes(),
            },
            authenticated=True,
        )
        data = self._data_object(payload)
        access_token = self._clean_string(data.get("accessToken"))
        refresh_token = self._clean_string(data.get("refreshToken"))
        if access_token and refresh_token:
            self._tokens = OppdTokens(access_token, refresh_token)
        if not self._tokens.access_token or not self._tokens.refresh_token:
            raise OppdResponseError("MyOPPD MFA verification did not return tokens")
        return self._tokens

    async def async_get_accounts(self) -> list[OppdAccount]:
        """Discover accounts linked to the authenticated MyOPPD user."""
        url = ACCOUNT_ADDRESS_URL.format(user_uuid=quote(self.user_uuid, safe=""))
        payload = await self._async_get_json(url, authenticated=True)
        accounts: list[OppdAccount] = []
        for row in self._data_list(payload):
            account_number = self._clean_string(row.get("accountNumber"))
            if not account_number:
                continue
            service_address = row.get("serviceAddress")
            if not isinstance(service_address, dict):
                service_address = row
            nickname = self._clean_string(row.get("nickName"))
            address = self._clean_string(service_address.get("address1"))
            city = self._clean_string(service_address.get("city"))
            label_parts = [nickname or f"Account ending {account_number[-4:]}"]
            if address:
                label_parts.append(address)
            if city:
                label_parts.append(city)
            accounts.append(
                OppdAccount(
                    account_number=account_number,
                    label=" — ".join(label_parts),
                    premise_number=self._clean_string(
                        service_address.get("premiseNumber")
                    ),
                    is_default=(
                        row.get("default") is True or row.get("isDefault") is True
                    ),
                )
            )
        accounts.sort(key=lambda account: (not account.is_default, account.label))
        return accounts

    async def async_get_meters(self, account_number: str) -> list[OppdMeter]:
        """Discover meters for a MyOPPD account."""
        query = urlencode({"AccountNumber": account_number})
        payload = await self._async_get_json(
            f"{METER_DISCOVERY_URL}?{query}",
            authenticated=True,
        )
        meters: dict[str, OppdMeter] = {}
        for row in self._data_list(payload):
            meter_number = self._clean_string(row.get("meterNumber"))
            if not meter_number:
                continue
            raw_ami = row.get("amiMeter")
            meters[meter_number] = OppdMeter(
                meter_number=meter_number,
                meter_type=self._clean_string(
                    row.get("meterSubType") or row.get("meterType")
                ),
                is_ami=(
                    raw_ami is True
                    or str(raw_ami).strip().lower() in ("1", "true", "yes")
                ),
            )
        return sorted(meters.values(), key=lambda meter: meter.meter_number)

    async def async_refresh_tokens(self) -> OppdTokens:
        """Refresh and rotate the MyOPPD access and refresh tokens."""
        payload = await self._async_post_json(
            REFRESH_URL,
            {"refreshToken": self._tokens.refresh_token},
            authenticated=True,
        )
        data = self._data_object(payload)

        access_token = self._clean_string(data.get("accessToken"))
        refresh_token = self._clean_string(data.get("refreshToken"))

        if not access_token or not refresh_token:
            raise OppdResponseError(
                "MyOPPD token refresh did not return both rotated tokens"
            )

        self._tokens = OppdTokens(access_token, refresh_token)
        return self._tokens

    async def async_get_electric_usage(
        self,
        start: date,
        end: date,
        *,
        periodicity: str = PERIODICITY_DAILY,
    ) -> list[OppdUsageRecord]:
        """Return normalized electric usage for the requested date range."""
        body: dict[str, Any] = {
            "accountNumber": self.account_number,
            "from": start.isoformat(),
            "to": end.isoformat(),
            "uom": "kWh",
            "periodicity": periodicity,
        }
        if self.meter_number:
            body["meterNumber"] = self.meter_number

        try:
            payload = await self._async_post_json(
                ELECTRIC_USAGE_URL,
                body,
                authenticated=True,
            )
        except OppdNoDataError:
            return []
        data = self._data_object(payload)
        raw_records = data.get("electricUsages")

        if raw_records is None and isinstance(payload.get("electricUsages"), list):
            raw_records = payload["electricUsages"]

        if not isinstance(raw_records, list):
            raise OppdResponseError(
                "MyOPPD usage response did not contain electricUsages"
            )

        records = [
            record
            for row in raw_records
            if isinstance(row, dict)
            if (record := self._normalize_usage(row)) is not None
        ]
        records.sort(key=lambda record: record.start)
        return self._combine_intervals(records)

    async def async_validate(self) -> list[OppdUsageRecord]:
        """Validate the current access token without rotating refresh state."""
        today = datetime.now(_OPPD_TIME_ZONE).date()
        records = await self.async_get_electric_usage(
            today - timedelta(days=400), today
        )
        if records:
            return records
        return await self.async_get_electric_usage(
            today - timedelta(days=400),
            today,
            periodicity="MO",
        )

    async def _async_post_json(
        self,
        url: str,
        body: dict[str, Any],
        *,
        authenticated: bool,
    ) -> dict[str, Any]:
        return await self._async_request_json(
            "POST",
            url,
            body,
            authenticated=authenticated,
        )

    async def _async_get_json(
        self,
        url: str,
        *,
        authenticated: bool,
    ) -> dict[str, Any]:
        """Issue a GET and return its JSON object."""
        return await self._async_request_json(
            "GET",
            url,
            None,
            authenticated=authenticated,
        )

    async def _async_request_json(
        self,
        method: str,
        url: str,
        body: dict[str, Any] | None,
        *,
        authenticated: bool,
    ) -> dict[str, Any]:
        """Issue an app-shaped MyOPPD API request."""
        headers = self._headers(authenticated=authenticated)
        async with self._session.request(
            method,
            url,
            json=body if method != "GET" else None,
            headers=headers,
            timeout=_TIMEOUT,
        ) as response:
            payload = await self._response_json(response)
            self._raise_for_error(response, payload)
            return payload

    def _headers(self, *, authenticated: bool) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "PlatformType": "2",
            "PT": "1",
            "uid": "1",
            "ln_code": "EN_US",
            "mav": APP_VERSION,
            "Referer": "",
            "User-Agent": USER_AGENT,
        }
        if authenticated:
            headers["Authorization"] = self._bearer(self._tokens.access_token)
            if self.user_uuid:
                headers["uuid"] = self.user_uuid
        else:
            headers["ST"] = "PL"
        return headers

    @staticmethod
    async def _response_json(response: ClientResponse) -> dict[str, Any]:
        try:
            payload = await response.json(content_type=None)
        except (ValueError, TypeError) as err:
            body = (await response.text())[:300]
            raise OppdResponseError(
                f"MyOPPD returned non-JSON HTTP {response.status}: {body}"
            ) from err

        if not isinstance(payload, dict):
            raise OppdResponseError("MyOPPD returned an unexpected JSON response")
        return payload

    @staticmethod
    def _raise_for_error(
        response: ClientResponse,
        payload: dict[str, Any],
    ) -> None:
        status = payload.get("status")
        inner_code: int | None = None
        inner_error = False
        message = ""
        msg_code = ""

        if isinstance(status, dict):
            try:
                inner_code = int(status.get("code"))
            except (TypeError, ValueError):
                inner_code = None
            inner_error = status.get("error") is True
            message = str(status.get("message") or "").strip()
            msg_code = str(status.get("msgcode") or "").strip()

        is_error = response.status >= 400 or inner_error
        if not is_error and inner_code is not None:
            is_error = inner_code < 200 or inner_code >= 300
        if not is_error:
            return

        detail = message or msg_code or f"HTTP {response.status}"
        if "no data found" in message.lower():
            raise OppdNoDataError(f"MyOPPD returned no usage data: {detail}")
        auth_markers = ("unauthorized", "token", "auth")
        if (
            response.status in (401, 403)
            or inner_code in (401, 403)
            or any(marker in f"{message} {msg_code}".lower() for marker in auth_markers)
        ):
            raise OppdAuthError(f"MyOPPD authentication failed: {detail}")
        raise OppdApiError(f"MyOPPD request failed: {detail}")

    @staticmethod
    def _data_object(payload: dict[str, Any]) -> dict[str, Any]:
        data = payload.get("data")
        return data if isinstance(data, dict) else payload

    @staticmethod
    def _data_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Return a list of objects from common MyOPPD response envelopes."""
        value: Any = payload.get("data", payload)
        if isinstance(value, dict):
            for key in ("data", "accounts", "meters", "serviceAddresses"):
                candidate = value.get(key)
                if isinstance(candidate, list):
                    value = candidate
                    break
        if not isinstance(value, list):
            raise OppdResponseError("MyOPPD discovery response did not contain a list")
        return [row for row in value if isinstance(row, dict)]

    @staticmethod
    def _custom_attributes() -> dict[str, Any]:
        """Return the device metadata sent by the MyOPPD Android app."""
        return {
            "ip": "",
            "os": "Android",
            "client": "Android",
            "version": "14",
            "deviceId": "",
            "deviceName": "Home Assistant",
            "source": 1,
            "seviceName": "Android",
        }

    @classmethod
    def _optional_contact(cls, value: Any) -> str | None:
        cleaned = cls._clean_string(value)
        if cleaned is None or cleaned.lower() == "none":
            return None
        return cleaned

    @classmethod
    def _normalize_usage(
        cls,
        row: dict[str, Any],
    ) -> OppdUsageRecord | None:
        start = cls._parse_datetime(row.get("readingFrom"))
        if start is None:
            return None

        consumption = cls._to_kwh(row.get("consumption"), row.get("uom"))
        if consumption is None:
            return None

        return OppdUsageRecord(
            start=start,
            end=cls._parse_datetime(row.get("readingTo")),
            consumption_kwh=consumption,
            meter_number=cls._clean_string(row.get("meterNumber")),
        )

    @staticmethod
    def _combine_intervals(
        records: list[OppdUsageRecord],
    ) -> list[OppdUsageRecord]:
        """Combine per-meter rows that describe the same interval."""
        combined: dict[tuple[datetime, datetime | None], OppdUsageRecord] = {}
        for record in records:
            key = (record.start, record.end)
            previous = combined.get(key)
            if previous is None:
                combined[key] = record
                continue
            combined[key] = OppdUsageRecord(
                start=record.start,
                end=record.end,
                consumption_kwh=(previous.consumption_kwh + record.consumption_kwh),
                meter_number=None,
            )
        return sorted(combined.values(), key=lambda record: record.start)

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if not isinstance(value, str) or not value.strip():
            return None
        text = value.strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_OPPD_TIME_ZONE)
        return parsed.astimezone(timezone.utc).replace(
            minute=0,
            second=0,
            microsecond=0,
        )

    @staticmethod
    def _to_kwh(value: Any, unit: Any) -> float | None:
        try:
            amount = Decimal(str(value).replace(",", "").strip())
        except (InvalidOperation, AttributeError, ValueError):
            return None

        normalized_unit = str(unit or "kWh").strip().lower()
        if normalized_unit == "wh":
            amount /= Decimal(1000)
        elif normalized_unit == "mwh":
            amount *= Decimal(1000)
        elif normalized_unit not in ("kwh", "kw h"):
            return None
        return float(amount)

    @staticmethod
    def _bearer(token: str) -> str:
        token = token.strip()
        return token if token.lower().startswith("bearer ") else f"Bearer {token}"

    @staticmethod
    def _clean_string(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        value = value.strip()
        return value or None
