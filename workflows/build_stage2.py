"""Builds the Stage 2 Shift Desk workflow: Stage 1 (intake + Claude triage) plus the ticket log and team emails.

    python3 workflows/build_stage2.py   ->  workflows/shift-desk-stage2.json

Stage 2 keeps the same workflow id as Stage 1 so the form and test-webhook URLs don't change.
Everything after "Ticket Ready" is new:

    Ticket Ready -> Read Teams Tab -> Plan Notifications -> Send Email? -> Email Team -> Build Log Row
                                                                      \-> (no email) ->/
                    Build Log Row -> Log to Tickets Tab -> Check for Problems -> Alert Needed? -> Send Alert -> Return Ticket
                                                                                         \-> (no problems) ------>/
    Return Ticket is what the webhook / form gets back. Crashes go to the alerts workflow via settings.errorWorkflow.

No secrets live here: credentials are referenced by their local n8n id, and team email
addresses live only in the private Google Sheet (Teams tab), never in this repo.
"""
import json
import pathlib
import uuid

import build_stage1 as s1

HERE = pathlib.Path(__file__).parent
SHEET_ID = "1QBU8bWCPQMgwoOI-odJ0nk74Jc2U2t38sg0Qk84zeWw"   # private "Shift Desk Tickets" sheet
SHEETS_CRED = {"id": "lYW29juTFJlaS3ff", "name": "GoogleSheets_ShiftDesk"}
GMAIL_CRED = {"id": "hHZYU0Y0h6zkcnGv", "name": "Gmail_ShiftDesk"}
ALERTS_WORKFLOW_ID = "shiftDeskAlerts"     # built by build_alerts.py; deploy that one first

LOG_COLUMNS = ["ticket_id", "received_at", "source", "test_id", "from", "room", "message", "category", "team",
               "urgency", "urgency_note", "summary", "language", "confidence", "route", "escalate",
               "review_reason", "emailed_to", "model", "input_tokens", "output_tokens", "latency_ms", "error"]

# ── Code node 4: decide who gets the email and write it ──
PLAN_JS = r"""
// Runs once per ticket. Input = the rows of the Teams tab. Works out who to email and writes the email.
const t = $('Ticket Ready').first().json;
const rows = $input.all().map((i) => i.json);
const DM = 'Duty Manager';

const emailOf = {};
for (const r of rows) {
  if (r.team && r.email) emailOf[String(r.team).trim()] = String(r.email).trim();
}

// REVIEW tickets go to the Duty Manager. AUTO tickets go to the team; URGENT ones also copy the Duty Manager.
let toTeam = t.route === 'REVIEW' ? DM : t.team;
let ccTeam = t.escalate && toTeam !== DM ? DM : '';
const notes = [];
if (rows.some((r) => r.error)) notes.push('Could not read the Teams tab');
if (!emailOf[toTeam] && toTeam !== DM) {          // team missing from the sheet: don't drop it, send to the DM
  notes.push('No email for ' + toTeam + ' on the Teams tab, sent to ' + DM + ' instead');
  toTeam = DM; ccTeam = '';
}
const to = emailOf[toTeam] || '';
const cc = ccTeam ? (emailOf[ccTeam] || '') : '';

let sendEmail = true, skipReason = '';
if (t.test_id) { sendEmail = false; skipReason = 'Not sent (test run ' + t.test_id + ')'; }
else if (!to) { sendEmail = false; skipReason = 'NOT SENT: no email address for ' + toTeam; }

const tag = t.escalate ? '[URGENT] ' : t.route === 'REVIEW' ? '[REVIEW] ' : '[' + t.urgency + '] ';
const local = DateTime.fromISO(t.received_at).setZone('Pacific/Honolulu').toFormat('yyyy-LL-dd h:mm a');
const lines = [
  'Ticket ' + t.ticket_id + '  |  received ' + local,
  '',
  'Team:      ' + t.team + (t.route === 'REVIEW' ? '  (needs a person to check first)' : ''),
  'Urgency:   ' + t.urgency + (t.urgency_note ? ' - ' + t.urgency_note : ''),
  'Room:      ' + (t.room || 'not given'),
  'From:      ' + t.from,
  '',
  'Summary:   ' + (t.summary || '(no summary)'),
];
if (t.route === 'REVIEW') lines.push('Why review: ' + t.review_reason);
lines.push('', 'Original message:', t.message, '', '--',
  'Sorted automatically by Shift Desk (Claude, confidence ' + (t.confidence ?? 'n/a') + ').',
  'Demo system: all requests are fake test data.');

const subject = tag + t.ticket_id + ' - ' + t.team + ' - Room ' + (t.room || '?') + ' - ' + (t.summary || t.message).slice(0, 80);

return [{ json: {
  send_email: sendEmail,
  skip_reason: skipReason,
  mail_to: to,
  mail_cc: cc,
  mail_subject: subject,
  mail_body: lines.join('\n'),
  emailed_to: toTeam + (cc ? ' (cc ' + ccTeam + ')' : ''),    // team names only, never addresses
  notify_note: notes.join('; '),
} }];
"""

# ── Code node 5: one row for the Tickets tab, including what happened to the email ──
LOG_ROW_JS = r"""
// Reached from the Gmail node (sent or failed) or straight from "Send Email?" (no email needed).
const t = $('Ticket Ready').first().json;
const p = $('Plan Notifications').first().json;
const got = $input.first().json;
const COLUMNS = __COLUMNS__;

let emailed;
if (!p.send_email) emailed = p.skip_reason;
else if (got.id) emailed = 'Sent to ' + p.emailed_to;                 // Gmail returns the message id on success
else {
  const e = got.error;
  emailed = 'FAILED to email ' + p.emailed_to + ': ' + String((e && e.message) || e || 'unknown error').slice(0, 200);
}
if (p.notify_note) emailed += ' [' + p.notify_note + ']';

const row = { ...t, emailed_to: emailed };
row.received_at = DateTime.fromISO(t.received_at).setZone('Pacific/Honolulu').toFormat('yyyy-LL-dd HH:mm:ss');
const out = {};
for (const c of COLUMNS) out[c] = row[c] === null || row[c] === undefined ? '' : row[c];
return [{ json: out }];
"""

# ── Code node 6: did anything fail along the way? (handled failures never crash the run) ──
PROBLEMS_JS = r"""
// The ticket made it through, but each step may have failed quietly. Collect those failures here.
const t = $('Ticket Ready').first().json;
const row = $('Build Log Row').first().json;
const p = $('Plan Notifications').first().json;
const req = $('Validate & Build Request').first().json;
const logged = $input.first().json;

const problems = [];
if (t.error) problems.push('Claude step failed, ticket sent to the Duty Manager for review: ' + t.error);
if (/^(FAILED|NOT SENT)/.test(row.emailed_to)) problems.push('Team email: ' + row.emailed_to);
if (p.notify_note) problems.push(p.notify_note);
// F7: on success the sheet step returns the row, which has its own "error" column (Claude's error),
// so "has an error field" doesn't mean the write failed. A failed write returns no ticket_id.
const logError = logged.ticket_id ? '' : String((logged.error && logged.error.message) || logged.error || 'unknown error').slice(0, 200);
if (logError) problems.push('Ticket NOT logged to the sheet: ' + logError);

// Test runs don't alert (a 40-request test shouldn't flood your inbox), unless it's a simulated failure.
const alert = problems.length > 0 && (!t.test_id || !!req.simulate_failure);
return [{ json: { ...t, emailed_to: row.emailed_to, problems, alert, logged: !logError, log_error: logError } }];
"""

# ── Code node 7: what the form / test webhook gets back ──
RETURN_JS = r"""
// The webhook answers with the last node, so this returns the full ticket plus what Stage 2 did with it.
const c = $('Check for Problems').first().json;
const out = { ...c };
delete out.alert;
out.alerted = c.alert ? ($input.first().json.error ? 'FAILED to send alert' : 'Alert sent') : '';
return [{ json: out }];
"""


def js(code):
    return s1.js(code.replace("__COLUMNS__", json.dumps(LOG_COLUMNS)))


def rl(value, mode):
    return {"__rl": True, "value": value, "mode": mode}


node = s1.node
x = 1540   # new nodes start to the right of "Ticket Ready"
stage2_nodes = [
    node("Read Teams Tab", "n8n-nodes-base.googleSheets", 4.7, [x, 100], {
        "operation": "read",
        "documentId": rl(SHEET_ID, "id"),
        "sheetName": rl("Teams", "name"),
        "options": {},
    }, credentials={"googleSheetsOAuth2Api": SHEETS_CRED},
        alwaysOutputData=True, retryOnFail=True, maxTries=2, waitBetweenTries=2000, onError="continueRegularOutput"),

    node("Plan Notifications", "n8n-nodes-base.code", 2, [x + 260, 100],
         {"mode": "runOnceForAllItems", "jsCode": js(PLAN_JS)}),

    node("Send Email?", "n8n-nodes-base.if", 2.3, [x + 520, 100], {
        "conditions": {
            "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 2},
            "conditions": [{"id": str(uuid.uuid5(uuid.NAMESPACE_URL, "shift-desk/send-email")),
                            "leftValue": "={{ $json.send_email }}", "rightValue": "",
                            "operator": {"type": "boolean", "operation": "true", "singleValue": True}}],
            "combinator": "and"},
        "options": {}}),

    # No retry on purpose: if Gmail sent the email but the reply got lost, a retry would email the team twice.
    node("Email Team", "n8n-nodes-base.gmail", 2.2, [x + 780, 0], {
        "resource": "message", "operation": "send",
        "sendTo": "={{ $json.mail_to }}",
        "subject": "={{ $json.mail_subject }}",
        "emailType": "text",                       # plain text: a guest's message can't inject HTML into the email
        "message": "={{ $json.mail_body }}",
        "options": {"appendAttribution": False, "senderName": "Shift Desk",
                    "ccList": "={{ $json.mail_cc }}"},
    }, credentials={"gmailOAuth2": GMAIL_CRED}, onError="continueErrorOutput"),

    node("Build Log Row", "n8n-nodes-base.code", 2, [x + 1040, 100],
         {"mode": "runOnceForAllItems", "jsCode": js(LOG_ROW_JS)}),

    node("Log to Tickets Tab", "n8n-nodes-base.googleSheets", 4.7, [x + 1300, 100], {
        # F6: "append or update" matched on ticket_id, not plain "append". A retry after Google saved the
        # row but the reply got lost used to add the same ticket twice; now it just rewrites the same row.
        "operation": "appendOrUpdate",
        "documentId": rl(SHEET_ID, "id"),
        "sheetName": rl("Tickets", "name"),
        "columns": {"mappingMode": "autoMapInputData", "value": {}, "matchingColumns": ["ticket_id"], "schema": []},
        # RAW = store exactly what the guest typed. With USER_ENTERED, a message starting with "=" would
        # run as a spreadsheet formula (formula injection).
        "options": {"cellFormat": "RAW", "handlingExtraData": "ignoreIt"},
    }, credentials={"googleSheetsOAuth2Api": SHEETS_CRED},
        retryOnFail=True, maxTries=2, waitBetweenTries=2000, onError="continueRegularOutput"),

    node("Check for Problems", "n8n-nodes-base.code", 2, [x + 1560, 100],
         {"mode": "runOnceForAllItems", "jsCode": js(PROBLEMS_JS)}),

    node("Alert Needed?", "n8n-nodes-base.if", 2.3, [x + 1820, 100], {
        "conditions": {
            "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 2},
            "conditions": [{"id": str(uuid.uuid5(uuid.NAMESPACE_URL, "shift-desk/alert-needed")),
                            "leftValue": "={{ $json.alert }}", "rightValue": "",
                            "operator": {"type": "boolean", "operation": "true", "singleValue": True}}],
            "combinator": "and"},
        "options": {}}),

    # Hands the problem to the "Shift Desk Alerts" workflow. If even that fails, the guest's ticket
    # still gets its answer; the failure shows up as alerted = "FAILED to send alert".
    node("Send Alert", "n8n-nodes-base.executeWorkflow", 1.2, [x + 2080, 0], {
        "source": "database",
        "workflowId": rl(ALERTS_WORKFLOW_ID, "id"),
        "mode": "once",
        "options": {"waitForSubWorkflow": True},
    }, onError="continueRegularOutput"),

    node("Return Ticket", "n8n-nodes-base.code", 2, [x + 2340, 100],
         {"mode": "runOnceForAllItems", "jsCode": js(RETURN_JS)}),
]


def link(to):
    return [{"node": to, "type": "main", "index": 0}]


connections = dict(s1.connections)
connections.update({
    "Ticket Ready": {"main": [link("Read Teams Tab")]},
    "Read Teams Tab": {"main": [link("Plan Notifications")]},
    "Plan Notifications": {"main": [link("Send Email?")]},
    "Send Email?": {"main": [link("Email Team"), link("Build Log Row")]},
    "Email Team": {"main": [link("Build Log Row"), link("Build Log Row")]},   # sent, or failed
    "Build Log Row": {"main": [link("Log to Tickets Tab")]},
    "Log to Tickets Tab": {"main": [link("Check for Problems")]},
    "Check for Problems": {"main": [link("Alert Needed?")]},
    "Alert Needed?": {"main": [link("Send Alert"), link("Return Ticket")]},
    "Send Alert": {"main": [link("Return Ticket")]},
})

workflow = dict(s1.workflow)
workflow.update({
    "name": "Shift Desk (Stage 2: triage + ticket log + team emails)",
    "nodes": s1.nodes + stage2_nodes,
    "connections": connections,
    # If a run crashes outright, n8n starts the alerts workflow's "Run Crashed" trigger.
    "settings": {**s1.workflow["settings"], "errorWorkflow": ALERTS_WORKFLOW_ID},
})

out = HERE / "shift-desk-stage2.json"
out.write_text(json.dumps(workflow, indent=2, ensure_ascii=False))
print("wrote", out)
