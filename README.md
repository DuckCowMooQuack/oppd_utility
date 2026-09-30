# OPPD for Home Assistant

Experimental Home Assistant integration for importing MyOPPD electric usage
into Home Assistant long-term statistics and the Energy Dashboard.

## Setup

The normal setup flow signs in with a MyOPPD username and password, presents
the MFA methods enabled on the account, and discovers linked OPPD accounts and
meters. The password is used only for that setup request and is not stored by
Home Assistant. The resulting access and refresh tokens are rotated and saved
automatically.

Existing manual-token entries can use **Reconfigure** from the integration's
menu to switch to the normal sign-in flow without changing their statistic ID
or Energy Dashboard configuration.

Manual token capture remains available as an advanced fallback. See
[Token setup](docs/token-setup.md) for Chrome and Edge instructions, security
precautions, and common failure cases.

The integration requests daily interval usage in kWh and publishes it as the
external statistic `oppd_utility:electric_consumption_<account_number>`. Add
that statistic to **Settings → Dashboards → Energy → Grid consumption**.
When MyOPPD only returns monthly billing intervals, the integration evenly
allocates each reported total over the interval's actual days. This preserves
the billing-period total and prevents two billing cycles from being combined
into one calendar month. Daily values in that case are estimates; they are not
daily readings supplied by OPPD.

The latest-usage sensor is display-only and deliberately has no state class.
This prevents Home Assistant from generating a second, incorrectly timestamped
statistics stream at poll time.

Cost is not imported because the current MyOPPD usage response appears to
report unusable zero-dollar values. Leave the Energy Dashboard cost statistic
and price fields empty.

## Development installation

Copy `custom_components/oppd_utility` into Home Assistant's `/config/custom_components`
directory and restart Home Assistant. Add **OPPD** from
**Settings → Devices & services**.

Do not paste account credentials or tokens into issue reports or logs.
