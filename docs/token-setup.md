# Manual MyOPPD token setup

> **Advanced fallback:** Normal setup signs in to MyOPPD and handles MFA,
> account discovery, and token storage automatically. Use these instructions
> only if normal sign-in is unavailable. These tokens provide access to your
> utility account and must be protected like a password.

The instructions below apply to current Chromium-based browsers such as Google
Chrome and Microsoft Edge. Browser labels can change slightly between versions.

## Before you begin

You need:

- Your MyOPPD username, password, and MFA method.
- Your OPPD account number, available on a bill or in MyOPPD account details.
- A desktop installation of Chrome or Edge.

Use a private/incognito browser window. This creates a separate MyOPPD session
so Home Assistant does not take over the refresh token used by your normal
browser session.

## Capture a matching token pair

1. Open a new private window:
   - Chrome: menu **⋮ → New Incognito window**.
   - Edge: menu **⋯ → New InPrivate window**.
2. Navigate to <https://www.myoppd.com/>. Do not sign in yet.
3. Open Developer Tools with **F12** or **Ctrl+Shift+I**.
4. Select the **Network** panel.
5. Enable **Preserve log** and select the **Fetch/XHR** filter.
6. Sign in to MyOPPD and complete MFA if prompted.
7. In the Network filter box, search for `auth`.
8. Select the successful `POST` request whose URL ends with:

   ```text
   /Usermanagementservice/api/1/login/auth
   ```

9. Open its **Response** or **Preview** tab and search within the response for
   `accessToken`. Current successful responses contain either:

   ```text
   data → accessToken
   data → refreshToken
   data → user → uuid
   ```

   or tokens nested under `data → tokenDetails`. Prefer the response that also
   includes `data.user.uuid` and indicates MFA is complete.

10. Copy the complete values without the surrounding quotation marks. Keep
    both tokens from the same response. Also copy `data.user.uuid`; Home
    Assistant sends it as MyOPPD's authenticated `uuid` header.

If the login response does not contain tokens because MFA completed in a
separate call, inspect the successful request ending in `/api/1/users/VerifyTfa`.
If the page has already refreshed its session, inspect the newest successful
request ending in `/api/1/Login/auth/refresh`; that response normally contains
`data.accessToken` and `data.refreshToken`.

## Add the integration

1. In Home Assistant, open **Settings → Devices & services**.
2. Select **Add integration** and search for **OPPD**.
3. Select **Manual token setup**.
4. Enter:
   - **Account number:** Your OPPD electric account number.
   - **Meter number:** Leave blank unless you deliberately want one meter from
     a multi-meter account.
   - **User UUID:** The `data.user.uuid` value from the same login response.
   - **Access token:** The complete `accessToken` value.
   - **Refresh token:** The matching `refreshToken` value.
5. Submit the form immediately. Setup validates the pair, rotates both tokens,
   and saves the new pair in the Home Assistant config entry.
6. After setup succeeds, close the private window **without selecting Log out**.
   Logging out can revoke the session Home Assistant just adopted.

## Important token behavior

- Access and refresh tokens must come from the same response.
- MyOPPD rotates refresh tokens. After any newer refresh response, an older
  copied pair may no longer work.
- Do not let the private window continue making MyOPPD requests while adding
  the integration; it could rotate the token before Home Assistant uses it.
- Do not paste tokens into chat, email, screenshots, issue reports, or logs.
- Do not use **Copy as cURL** when asking for help. It can expose tokens,
  cookies, account identifiers, and other private headers.
- If setup reports an authentication or connection error, close the private
  window and repeat the procedure in a fresh one to obtain the newest pair.

## Confirm Energy Dashboard data

After the first successful refresh, go to
**Settings → Dashboards → Energy → Grid consumption** and select the statistic
named similar to:

```text
OPPD 1234 Electric Consumption
```

The integration imports daily consumption as kWh using the original meter
interval timestamps. The latest-consumption sensor is informational and should
not be selected as the Energy Dashboard source.
