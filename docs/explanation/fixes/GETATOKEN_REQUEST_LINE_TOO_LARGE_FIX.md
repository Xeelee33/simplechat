# GetAToken Request Line Too Large Fix

## Issue Description

Some users intermittently saw a browser error during sign-in:

`Bad Request: Request Line is too large (4501 > 4094)`

The error appeared on the `/getAToken?code=...` Microsoft Entra callback URL when the authorization response carried a long authorization code in the query string.

## Root Cause Analysis

SimpleChat requested the default OAuth query response mode for interactive sign-in and Microsoft Graph consent flows. When Microsoft Entra returned a long authorization code, the callback URL could exceed the request-line limit enforced by the front-end server before Flask could handle the request.

## Version Implemented

- Fixed in version: **0.261.007**

## Technical Details

- Files modified:
  - `application/single_app/route_frontend_authentication.py`
  - `application/single_app/functions_authentication.py`
  - `application/single_app/config.py`
  - `functional_tests/test_getatoken_missing_code_redirect.py`

- Code changes summary:
  - Requested `response_mode="form_post"` when generating Microsoft Entra authorization URLs for normal login and incremental Microsoft Graph consent.
  - Allowed `/getAToken` and `/getATokenApi` to accept both `GET` and `POST` callbacks.
  - Read callback values from `request.values` so existing query callbacks and new form-post callbacks are both supported.
  - Logged provider callback errors server-side while returning stable, non-sensitive messages to the browser.
  - Bumped the application version from `0.261.006` to `0.261.007`.

## Testing Approach

- Added AST-based regression coverage to verify that `/getAToken` supports POST callbacks and reads the authorization code from `request.values`.
- Added regression coverage to verify that interactive authorization URL generation requests `form_post` response mode.

## Impact Analysis

- User experience:
  - Login callbacks no longer put the authorization code in the URL request line, reducing the chance of intermittent server rejection for users with long callback payloads.

- Compatibility:
  - Existing GET callback handling remains in place for older or manually configured flows.

- Risk:
  - Low. The token exchange still uses the same authorization code and redirect URI; only the transport of callback parameters changes from query string to form body.
