"""Config flow for OPPD."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from aiohttp import ClientError
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    OppdAccount,
    OppdApi,
    OppdApiError,
    OppdAuthError,
    OppdLoginResult,
    OppdTokens,
)
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_ACCOUNT_NUMBER,
    CONF_HISTORY_DAYS,
    CONF_METER_NUMBER,
    CONF_PASSWORD,
    CONF_REFRESH_TOKEN,
    CONF_STATISTICS_VERSION,
    CONF_TFA_CODE,
    CONF_TFA_METHOD,
    CONF_UPDATE_INTERVAL_HOURS,
    CONF_USER_UUID,
    CONF_USERNAME,
    DEFAULT_HISTORY_DAYS,
    DEFAULT_UPDATE_INTERVAL_HOURS,
    DOMAIN,
    MAX_HISTORY_DAYS,
    MAX_UPDATE_INTERVAL_HOURS,
    MIN_HISTORY_DAYS,
    MIN_UPDATE_INTERVAL_HOURS,
    STATISTICS_VERSION,
)

_LOGGER = logging.getLogger(__name__)

_SECRET_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
_TFA_PHONE = "phone"
_TFA_EMAIL = "email"
_TFA_AUTHENTICATOR = "authenticator"
_TFA_MODES = {
    _TFA_PHONE: 1,
    _TFA_EMAIL: 2,
    _TFA_AUTHENTICATOR: 4,
}


class OppdUtilityConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle OPPD configuration."""

    VERSION = 1

    def __init__(self) -> None:
        self._api: OppdApi | None = None
        self._login_result: OppdLoginResult | None = None
        self._accounts: dict[str, OppdAccount] = {}
        self._selected_account: OppdAccount | None = None
        self._tfa_mode: int | None = None
        self._entry_to_update: ConfigEntry | None = None

    @staticmethod
    def async_get_options_flow(
        _config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return OppdUtilityOptionsFlow()

    async def async_step_user(
        self,
        _user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Offer normal sign-in and the advanced token fallback."""
        return self.async_show_menu(
            step_id="user",
            menu_options=["login", "manual"],
        )

    async def async_step_login(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Authenticate with a MyOPPD username and password."""
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            password = user_input[CONF_PASSWORD]
            try:
                self._api = OppdApi.for_login(async_get_clientsession(self.hass))
                self._login_result = await self._api.async_login(username, password)
                if self._login_result.tfa_details is not None:
                    if not self._tfa_options():
                        errors["base"] = "no_mfa_methods"
                    else:
                        return await self.async_step_mfa_method()
                else:
                    return await self._async_finish_authentication()
            except OppdAuthError as err:
                _LOGGER.warning("MyOPPD rejected setup authentication: %s", err)
                errors["base"] = "invalid_auth"
            except OppdApiError as err:
                _LOGGER.warning("MyOPPD setup login failed: %s", err)
                errors["base"] = "cannot_connect"
            except (ClientError, TimeoutError) as err:
                _LOGGER.warning("Unable to connect to MyOPPD during login: %s", err)
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="login",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): _SECRET_SELECTOR,
                }
            ),
            errors=errors,
        )

    async def async_step_mfa_method(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Select and initiate a MyOPPD MFA method."""
        options = self._tfa_options()
        errors: dict[str, str] = {}

        if user_input is not None:
            method = user_input[CONF_TFA_METHOD]
            self._tfa_mode = _TFA_MODES[method]
            try:
                if self._tfa_mode != _TFA_MODES[_TFA_AUTHENTICATOR]:
                    assert self._api is not None
                    await self._api.async_send_tfa(self._tfa_mode)
                return await self.async_step_mfa_code()
            except OppdAuthError as err:
                _LOGGER.warning("MyOPPD rejected the MFA request: %s", err)
                errors["base"] = "invalid_auth"
            except OppdApiError as err:
                _LOGGER.warning("Unable to send a MyOPPD MFA code: %s", err)
                errors["base"] = "cannot_connect"
            except (ClientError, TimeoutError) as err:
                _LOGGER.warning("Unable to connect to MyOPPD for MFA: %s", err)
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="mfa_method",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_TFA_METHOD,
                        default=options[0]["value"],
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=options,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_mfa_code(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Verify the MyOPPD MFA code."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                assert self._api is not None
                assert self._tfa_mode is not None
                await self._api.async_verify_tfa(
                    self._tfa_mode,
                    user_input[CONF_TFA_CODE],
                )
                return await self._async_finish_authentication()
            except OppdAuthError as err:
                _LOGGER.warning("MyOPPD rejected the MFA code: %s", err)
                errors["base"] = "invalid_mfa"
            except OppdApiError as err:
                _LOGGER.warning("MyOPPD MFA verification failed: %s", err)
                errors["base"] = "cannot_connect"
            except (ClientError, TimeoutError) as err:
                _LOGGER.warning("Unable to connect to MyOPPD for MFA: %s", err)
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="mfa_code",
            data_schema=vol.Schema({vol.Required(CONF_TFA_CODE): _SECRET_SELECTOR}),
            errors=errors,
        )

    async def async_step_account(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Choose an automatically discovered OPPD account."""
        if user_input is not None:
            self._selected_account = self._accounts[user_input[CONF_ACCOUNT_NUMBER]]
            return await self._async_prepare_meter()

        options = [
            SelectOptionDict(value=account.account_number, label=account.label)
            for account in self._accounts.values()
        ]
        return self.async_show_form(
            step_id="account",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_ACCOUNT_NUMBER,
                        default=options[0]["value"],
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=options,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_meter(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Choose all usage or one automatically discovered meter."""
        meters = self.context["discovered_meters"]
        if user_input is not None:
            return await self._async_create_or_update_entry(
                user_input[CONF_METER_NUMBER]
            )

        options = [SelectOptionDict(value="", label="All meters (recommended)")]
        options.extend(
            SelectOptionDict(
                value=meter.meter_number,
                label=(
                    f"{meter.meter_number} (AMI)"
                    if meter.is_ami
                    else meter.meter_number
                ),
            )
            for meter in meters
        )
        return self.async_show_form(
            step_id="meter",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_METER_NUMBER,
                        default="",
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=options,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_manual(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Configure one OPPD account using a manually captured token pair."""
        errors: dict[str, str] = {}

        if user_input is not None:
            user_input = self._clean_input(user_input)
            if not all(
                user_input.get(field)
                for field in (
                    CONF_ACCOUNT_NUMBER,
                    CONF_USER_UUID,
                    CONF_ACCESS_TOKEN,
                    CONF_REFRESH_TOKEN,
                )
            ):
                errors["base"] = "missing_required"
            else:
                try:
                    tokens = await self._async_validate(user_input)
                except OppdAuthError as err:
                    _LOGGER.warning("MyOPPD rejected setup authentication: %s", err)
                    errors["base"] = "invalid_auth"
                except OppdApiError as err:
                    _LOGGER.warning("MyOPPD setup validation failed: %s", err)
                    errors["base"] = "cannot_connect"
                except (ClientError, TimeoutError) as err:
                    _LOGGER.warning("Unable to connect to MyOPPD: %s", err)
                    errors["base"] = "cannot_connect"
                else:
                    account_number = user_input[CONF_ACCOUNT_NUMBER]
                    await self.async_set_unique_id(account_number)
                    self._abort_if_unique_id_configured()
                    user_input[CONF_ACCESS_TOKEN] = tokens.access_token
                    user_input[CONF_REFRESH_TOKEN] = tokens.refresh_token
                    user_input[CONF_STATISTICS_VERSION] = STATISTICS_VERSION
                    return self.async_create_entry(
                        title=f"OPPD {account_number[-4:]}",
                        data=user_input,
                    )

        return self.async_show_form(
            step_id="manual",
            data_schema=self._account_schema(user_input),
            errors=errors,
        )

    async def async_step_reauth(
        self,
        _entry_data: Mapping[str, Any],
    ) -> ConfigFlowResult:
        """Start username/password reauthentication."""
        self._entry_to_update = self._get_reauth_entry()
        return await self.async_step_login()

    async def async_step_reconfigure(
        self,
        _user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Convert an existing manual-token entry to normal sign-in."""
        self._entry_to_update = self._get_reconfigure_entry()
        return await self.async_step_login()

    async def _async_finish_authentication(self) -> ConfigFlowResult:
        """Discover accounts after login and continue the flow."""
        assert self._api is not None
        self._accounts = {
            account.account_number: account
            for account in await self._api.async_get_accounts()
        }
        if not self._accounts:
            return self.async_abort(reason="no_accounts")

        if self._entry_to_update is not None:
            account_number = self._entry_to_update.data[CONF_ACCOUNT_NUMBER]
            self._selected_account = self._accounts.get(account_number)
            if self._selected_account is None:
                return self.async_abort(reason="account_not_found")
            return await self._async_create_or_update_entry(
                self._entry_to_update.data.get(CONF_METER_NUMBER, "")
            )

        if len(self._accounts) == 1:
            self._selected_account = next(iter(self._accounts.values()))
            return await self._async_prepare_meter()
        return await self.async_step_account()

    async def _async_prepare_meter(self) -> ConfigFlowResult:
        """Discover meters and select account-wide or per-meter usage."""
        assert self._api is not None
        assert self._selected_account is not None
        try:
            meters = await self._api.async_get_meters(
                self._selected_account.account_number
            )
        except (OppdApiError, ClientError, TimeoutError) as err:
            _LOGGER.warning(
                "Unable to discover MyOPPD meters; using account-wide usage: %s",
                err,
            )
            meters = []

        if len(meters) <= 1:
            meter_number = meters[0].meter_number if meters else ""
            return await self._async_create_or_update_entry(meter_number)
        self.context["discovered_meters"] = meters
        return await self.async_step_meter()

    async def _async_create_or_update_entry(
        self,
        meter_number: str,
    ) -> ConfigFlowResult:
        """Persist only the resulting session and selected utility account."""
        assert self._api is not None
        assert self._selected_account is not None
        tokens = self._api.tokens
        account_number = self._selected_account.account_number
        updates = {
            CONF_ACCESS_TOKEN: tokens.access_token,
            CONF_REFRESH_TOKEN: tokens.refresh_token,
            CONF_USER_UUID: self._api.user_uuid,
            CONF_ACCOUNT_NUMBER: account_number,
            CONF_METER_NUMBER: meter_number,
            CONF_STATISTICS_VERSION: STATISTICS_VERSION,
        }

        if self._entry_to_update is not None:
            return self.async_update_reload_and_abort(
                self._entry_to_update,
                data_updates=updates,
            )

        await self.async_set_unique_id(account_number)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"OPPD {account_number[-4:]}",
            data=updates,
        )

    def _tfa_options(self) -> list[SelectOptionDict]:
        """Build the available MFA choices from the login response."""
        if self._login_result is None or self._login_result.tfa_details is None:
            return []
        details = self._login_result.tfa_details
        options: list[SelectOptionDict] = []
        if details.phone:
            options.append(
                SelectOptionDict(
                    value=_TFA_PHONE,
                    label=f"Text message ({details.phone})",
                )
            )
        if details.email:
            options.append(
                SelectOptionDict(
                    value=_TFA_EMAIL,
                    label=f"Email ({details.email})",
                )
            )
        if details.authenticator_enabled:
            options.append(
                SelectOptionDict(
                    value=_TFA_AUTHENTICATOR,
                    label="Authenticator app",
                )
            )
        return options

    async def _async_validate(self, data: Mapping[str, Any]) -> OppdTokens:
        session = async_get_clientsession(self.hass)
        api = OppdApi(
            session,
            data[CONF_ACCESS_TOKEN],
            data[CONF_REFRESH_TOKEN],
            data[CONF_USER_UUID],
            data[CONF_ACCOUNT_NUMBER],
            data.get(CONF_METER_NUMBER, ""),
        )
        await api.async_validate()
        return api.tokens

    @staticmethod
    def _clean_input(user_input: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value.strip() if isinstance(value, str) else value
            for key, value in user_input.items()
        }

    @staticmethod
    def _account_schema(defaults: Mapping[str, Any] | None) -> vol.Schema:
        defaults = defaults or {}
        return vol.Schema(
            {
                vol.Required(
                    CONF_ACCOUNT_NUMBER,
                    default=defaults.get(CONF_ACCOUNT_NUMBER, ""),
                ): str,
                vol.Optional(
                    CONF_METER_NUMBER,
                    default=defaults.get(CONF_METER_NUMBER, ""),
                ): str,
                vol.Required(
                    CONF_USER_UUID,
                    default=defaults.get(CONF_USER_UUID, ""),
                ): str,
                vol.Required(CONF_ACCESS_TOKEN): _SECRET_SELECTOR,
                vol.Required(CONF_REFRESH_TOKEN): _SECRET_SELECTOR,
            }
        )


class OppdUtilityOptionsFlow(config_entries.OptionsFlow):
    """Handle OPPD options."""

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_UPDATE_INTERVAL_HOURS,
                        default=self.config_entry.options.get(
                            CONF_UPDATE_INTERVAL_HOURS,
                            DEFAULT_UPDATE_INTERVAL_HOURS,
                        ),
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(
                            min=MIN_UPDATE_INTERVAL_HOURS,
                            max=MAX_UPDATE_INTERVAL_HOURS,
                        ),
                    ),
                    vol.Required(
                        CONF_HISTORY_DAYS,
                        default=self.config_entry.options.get(
                            CONF_HISTORY_DAYS,
                            DEFAULT_HISTORY_DAYS,
                        ),
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=MIN_HISTORY_DAYS, max=MAX_HISTORY_DAYS),
                    ),
                }
            ),
        )
