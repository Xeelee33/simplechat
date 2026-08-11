# SimpleChat Release QA Checklist

Use this checklist in development/staging before promoting any release candidate to production.

## Release Metadata

- Release version: 0.241.007
- Release candidate ID:
- Build commit SHA:
- Environment tested:
- QA owner:
- Product owner:
- Test start date/time:
- Test end date/time:

## 1. Deployment And Startup

- [ ] Correct artifact deployed from intended commit
- [ ] App starts cleanly with no crash loops
- [ ] External health endpoints return 200
- [ ] No fatal startup errors in App Service logs
- [ ] Critical environment variables are present and valid
- [ ] Slot identity is enabled and expected role assignments are present

## 2. Authentication And Authorization

- [ ] User login flow succeeds
- [ ] Admin login flow succeeds
- [ ] Logout and redirect flow succeeds
- [ ] Non-admin access to admin routes is denied
- [ ] Group-role protected actions enforce role checks
- [ ] Cross-user data access attempts are denied

## 3. Core Chat Functionality

- [ ] New chat request returns streamed response
- [ ] Retry and edit flows work
- [ ] Conversation history loads after refresh
- [ ] Citation modal opens and renders expected data
- [ ] Conversation export works for enabled formats

## 4. Model Endpoints And AI Connectivity

- [ ] New model endpoint can be created and saved
- [ ] Model fetch works for configured endpoint
- [ ] Model connection test succeeds
- [ ] Selected model is used in live chat requests
- [ ] Managed identity auth mode works (if enabled)
- [ ] API key auth mode works (if enabled)
- [ ] Government cloud environments persist management_cloud as government for hidden-cloud save paths
- [ ] No token audience or scope mismatch errors in runtime logs

## 5. Agents And Plugins

- [ ] Global agent selection works
- [ ] Personal agent scope toggle behavior is correct
- [ ] Group agent scope toggle behavior is correct
- [ ] Agent streaming path succeeds with selected model
- [ ] Plugin calls execute successfully when enabled
- [ ] Plugin authorization boundaries are enforced

## 6. Workspaces And Documents

- [ ] Personal workspace upload/list/delete works
- [ ] Group workspace documents and prompts load correctly
- [ ] Public workspace member and non-member behavior is correct
- [ ] Sharing workflows enforce ownership and role checks
- [ ] Tags create/update/render correctly across scopes

## 7. Search, Extraction, And Media

- [ ] Document ingestion and chunking complete successfully
- [ ] Search returns expected scope-constrained results
- [ ] Document Intelligence extraction works for sample docs
- [ ] Audio/video flows work when enabled
- [ ] Speech integration works for configured auth mode

## 8. Security Regression Checks

- [ ] Authorization regression set passes
- [ ] Stored XSS regression set passes
- [ ] No sensitive settings are exposed to frontend routes
- [ ] Admin-only destructive endpoints are protected
- [ ] No cross-scope endpoint/model leakage is observed

## 9. Observability And Reliability

- [ ] Expected telemetry and logs are emitted
- [ ] Error logs contain enough context for triage
- [ ] No sustained warning/error spam during normal operation
- [ ] App remains healthy during basic concurrent usage
- [ ] Notification/background jobs behave as expected

## 10. Performance Smoke

- [ ] Chat latency is within acceptable target
- [ ] No abnormal CPU/memory spikes under smoke load
- [ ] App remains stable during a short soak run

## Sign-Off

- [ ] QA pass
- [ ] Product owner approval
- [ ] Deployment owner approval
- [ ] Rollback plan verified

Decision:
- [ ] GO
- [ ] NO-GO

Notes:
-
-
-