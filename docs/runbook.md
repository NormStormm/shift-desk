# Shift Desk runbook

How to install, deploy, check, and fix Shift Desk. Written for whoever is on call, including future me.

## Install

n8n runs locally (tested on n8n 2.43.5, Node 24, macOS). It needs about 2.7 GB of disk.

```bash
mkdir ~/n8n-local && cd ~/n8n-local && npm install n8n
```

Start script (`~/n8n-local/start.sh`):

```bash
#!/bin/bash
export N8N_USER_FOLDER="$HOME/n8n-local/data"   # workflows, credentials, run history
export N8N_PORT=5678
export N8N_WEBHOOK_URL="http://localhost:5678/"
export GENERIC_TIMEZONE="Pacific/Honolulu"       # the 3 pm / 11 pm schedule uses this
export TZ="Pacific/Honolulu"
export N8N_DIAGNOSTICS_ENABLED=false             # no usage telemetry
export N8N_UNVERIFIED_PACKAGES_ENABLED=false     # no unverified community packages
export EXECUTIONS_DATA_PRUNE=true                # keep run history 14 days
export EXECUTIONS_DATA_MAX_AGE=336
exec "$HOME/n8n-local/node_modules/.bin/n8n" start
```

Open `http://localhost:5678` and create the owner account.

> **n8n only runs while this is running.** If the laptop sleeps at 11 pm, there's no 11 pm summary. A real deployment would run on an always-on server.

## Google sign-in

Self-hosted n8n needs your own Google OAuth client:

1. [Google Cloud console](https://console.cloud.google.com/) → new project → enable the **Google Sheets**, **Google Drive** and **Gmail** APIs.
2. **OAuth consent screen:** External, **Testing** mode, and add your own Google account as a test user.
3. **Credentials → Create OAuth client ID → Web application.** Authorized redirect URI: `http://localhost:5678/rest/oauth2-credential/callback`.
4. In n8n, create a **Google Sheets OAuth2** and a **Gmail OAuth2** credential with that client ID and secret, then click **Sign in with Google**. On the "Google hasn't verified this app" screen, choose **Continue**. It's your own app.

> ⚠️ **Testing-mode sign-ins expire after 7 days.** When they do, the sheet log and every email (including alerts) fail. **Fix:** n8n → Credentials → open each Google credential → **Reconnect**. To make it permanent, publish the consent screen (Google may require verification).

## Deploy

Every workflow is generated from a Python script; never hand-edit the JSON.

```bash
cd workflows
python3 build_alerts.py && python3 build_stage2.py && python3 build_summary.py
```

Import and publish, **alerts first** (the other two point to it, and n8n only calls *published* workflows):

```bash
export N8N_USER_FOLDER=$HOME/n8n-local/data
N8N=~/n8n-local/node_modules/.bin/n8n
$N8N import:workflow --input=shift-desk-alerts.json   && $N8N publish:workflow --id=shiftDeskAlerts
$N8N import:workflow --input=shift-desk-stage2.json   && $N8N publish:workflow --id=shiftDeskStage1
$N8N import:workflow --input=shift-desk-summary.json  && $N8N publish:workflow --id=shiftDeskSummary
```

Then **restart n8n**. Don't pipe these commands into `head`: the publish command hangs when its output is cut off.

### Check the new version is live before testing (F5)

n8n reports "ready" a few seconds before it switches to the new version. Compare the version you published with the one that actually ran:

```bash
DB=~/n8n-local/data/.n8n/database.sqlite
sqlite3 $DB "select id, activeVersionId from workflow_entity;"
sqlite3 $DB "select id, workflowId, status, workflowVersionId from execution_entity order by id desc limit 5;"
```

If the latest run shows the old version, wait a few seconds and test again.

## Test

| What | How | Costs |
|---|---|---|
| One request, by hand | `http://localhost:5678/form/shift-desk` (use a normal browser; embedded browsers can block the form, see F1) | ~1¢, sends a real team email |
| All 40 test requests | `node eval/run-tests.mjs` (or `node eval/run-tests.mjs T03 T25` for a few) | ~37¢; logged to the sheet, **no emails** |
| Re-grade an old run | `node eval/run-tests.mjs --rescore eval/results/<file>.json` | free |
| Side-by-side report | `python3 eval/make_report.py eval/results/<run1>.json eval/results/<run2>.json` → `eval/REPORT.md` | free |
| Shift summary now | `curl -X POST localhost:5678/webhook/shift-summary-test -H 'content-type: application/json' -d '{"hours":24,"include_tests":true,"send_email":true}'` | ~1¢ |
| Crash alert | POST to `/webhook/shift-desk-test` with `"test_id":"X1","simulate_failure":"crash"` | free, sends an alert |
| Quiet-failure alert | same, with `"simulate_failure":"claude_error"` | free, sends an alert |

Requests with a `test_id` never email teams and never alert, unless they set `simulate_failure`.

## When an alert arrives

| Subject | Means | Do this |
|---|---|---|
| **[SHIFT DESK DOWN]** | A run crashed; the request may not be logged or sent anywhere. | Open the run link in the email, read the guest message, and handle it by hand. Then fix the cause (the email suggests one). |
| **[SHIFT DESK PROBLEM] … Claude step failed** | Claude was down or rejected the call; the ticket went to the Duty Manager for review. | Sort it by hand. If it repeats, check the [Anthropic status page](https://status.anthropic.com/) and your API credit. |
| **[SHIFT DESK PROBLEM] … Team email: FAILED** | The ticket is logged but the team never got the email. | **Call the team.** If every email fails, the Google sign-in probably expired: Reconnect (see above). |
| **[SHIFT DESK PROBLEM] … NOT logged** | The team was emailed but the sheet write failed. | Add the row by hand from the email; check the Google sign-in. |
| **No alerts at all, but things look wrong** | The alert itself goes through Gmail, so an expired Google sign-in silences it too. | Check n8n → **Executions** for red runs. |

## Change who gets emails

Edit the **Teams** tab in the sheet. Changes take effect on the next request; no redeploy is needed. Team names must match exactly (`Engineering`, `Housekeeping`, `Food & Beverage`, `Front Desk`, `Accounting`, `Security`, `Duty Manager`). A team missing from the tab is sent to the Duty Manager, and an alert notes it.

## Remove everything

Stop n8n and delete `~/n8n-local`. In Google Cloud, delete the project (this revokes its sign-ins). In your Google account → Security → Third-party access, remove the app.
