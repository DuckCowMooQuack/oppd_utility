"""Constants for the OPPD integration."""

DOMAIN = "oppd_utility"

# The Android app sends platform plus versionCode, not its display version.
APP_VERSION = "And|61"
USER_AGENT = "okhttp/4.12.0"

AUTH_BASE_URL = "https://myoppd-svc.cxlive.ai/Usermanagementservice"
USAGE_BASE_URL = "https://oppd-apim.cxlive.ai/usageservice"

REFRESH_URL = f"{AUTH_BASE_URL}/api/1/Login/auth/refresh"
LOGIN_URL = f"{AUTH_BASE_URL}/api/1/login/auth"
SEND_TFA_URL = f"{AUTH_BASE_URL}/api/1/users/SendTfa"
VERIFY_TFA_URL = f"{AUTH_BASE_URL}/api/1/users/VerifyTfa"
ELECTRIC_USAGE_URL = f"{USAGE_BASE_URL}/api/V1/Electric"
ACCOUNT_ADDRESS_URL = (
    "https://oppd-apim.cxlive.ai/ServiceAPI/api/1/Addresses/User/{user_uuid}"
)
METER_DISCOVERY_URL = (
    "https://oppd-apim.cxlive.ai/ServiceAPI/api/1/account/GetPremisebyAcc"
)

CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_SETUP_METHOD = "setup_method"
CONF_TFA_METHOD = "tfa_method"
CONF_TFA_CODE = "tfa_code"
CONF_ACCESS_TOKEN = "access_token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_USER_UUID = "user_uuid"
CONF_ACCOUNT_NUMBER = "account_number"
CONF_METER_NUMBER = "meter_number"
CONF_UPDATE_INTERVAL_HOURS = "update_interval_hours"
CONF_HISTORY_DAYS = "history_days"
CONF_STATISTICS_VERSION = "statistics_version"

DEFAULT_UPDATE_INTERVAL_HOURS = 12
MIN_UPDATE_INTERVAL_HOURS = 4
MAX_UPDATE_INTERVAL_HOURS = 168

DEFAULT_HISTORY_DAYS = 400
MIN_HISTORY_DAYS = 30
MAX_HISTORY_DAYS = 1460

# Re-fetch recent intervals so delayed readings can be backfilled. The
# statistics importer anchors the recalculated cumulative sum to an existing
# record, keeping the series stable across refreshes.
CORRECTION_WINDOW_DAYS = 45

# Version 2 allocates MyOPPD monthly billing totals across their constituent
# days. Version 1 stored the entire total at readingFrom, which could combine
# two bills in one calendar month in Home Assistant's Energy Dashboard.
STATISTICS_VERSION = 2

PERIODICITY_DAILY = "DA"
STATISTIC_ID_PREFIX = "electric_consumption"
