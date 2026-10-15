"""Builds "Shift Desk Alerts": the workflow that emails you when Shift Desk breaks.

    python3 workflows/build_alerts.py   ->  workflows/shift-desk-alerts.json

Two ways in, one way out:

    Run Crashed (Error Trigger)          -\
                                          > Describe Problem -> Who Gets Alerts -> Email Alert
    Called by Shift Desk (sub-workflow)  -/

- "Run Crashed" fires when a Shift Desk run fails outright (n8n's built-in error workflow).
- "Called by Shift Desk" is for *handled* failures: the ticket still went through, but something
  along the way failed (Claude call, team email, sheet log). Those never crash the run, so
  n8n's error workflow alone would never hear about them.

The alert goes to the Gmail account the credential is signed in to (with +alerts added),
looked up at run time, so no email address is written in this repo.
Deploy this workflow (import + publish) BEFORE the main workflow, which points at it.
"""
import json
import pathlib

import build_stage1 as s1

HERE = pathlib.Path(__file__).parent
WORKFLOW_ID = "shiftDeskAlerts"
GMAIL_CRED = {"id": "hHZYU0Y0h6zkcnGv", "name": "Gmail_ShiftDesk"}

DESCRIBE_JS = r"""
// Turns either kind of failure into one plain-language alert.
const j = $input.first().json;

// Hints for the failures we expect. Google sign-ins in Testing mode expire every 7 days.
function hint(text) {
  const s = String(text || '').toLowerCase();
  if (s.includes('invalid_grant') || s.includes('unauthorized') || s.includes('401') || s.includes('token'))
    return 'Looks like a Google or Claude sign-in problem. In n8n: Credentials > open the Google ones > Reconnect. (Google test sign-ins expire every 7 days.)';
  if (s.includes('429') || s.includes('rate') || s.includes('overloaded') || s.includes('529'))
    return 'A service is busy or rate-limiting. Usually clears on its own; watch for repeats.';
  if (s.includes('model')) return 'The Claude model name was rejected. Check MODEL in build_stage1.py.';
  return '';
}

let subject, lines;
if (j.execution || j.trigger) {
  // From the Error Trigger: the whole run failed, so the request may not be logged anywhere.
  const ex = j.execution || {};
  const err = ex.error || (j.trigger && j.trigger.error) || {};
  const where = ex.lastNodeExecuted || 'the trigger';
  subject = '[SHIFT DESK DOWN] Run crashed at "' + where + '"';
  lines = [
    'A Shift Desk run crashed. The request may not have been logged or sent to any team.',
    'Open the run below to see the guest message, then handle it by hand.',
    '',
    'Failed at:  ' + where,
    'Error:      ' + (err.message || 'unknown'),
    'Run:        ' + (ex.url || 'execution ' + ex.id) + (ex.mode ? '  (' + ex.mode + ')' : ''),
  ];
  const h = hint(err.message);
  if (h) lines.push('', 'Likely fix: ' + h);
} else {
  // Called by Shift Desk: the ticket went through, but part of it failed.
  const problems = j.problems || [];
  subject = '[SHIFT DESK PROBLEM] ' + j.ticket_id + ': ' + (problems[0] || 'unknown problem');
  lines = ['Ticket ' + j.ticket_id + ' went through, but:'];
  for (const p of problems) lines.push('  - ' + p);
  lines.push('',
    'Team:      ' + j.team + '  (route ' + j.route + ')',
    'Urgency:   ' + j.urgency,
    'Room:      ' + (j.room || 'not given'),
    'Summary:   ' + (j.summary || '(none)'),
    '',
    'Original message:', j.message || '(empty)');
  const h = hint(problems.join(' '));
  if (h) lines.push('', 'Likely fix: ' + h);
  if (j.test_id) lines.push('', '(Test run ' + j.test_id + '.)');
}
lines.push('', '--', 'Shift Desk alerts. Demo system: all requests are fake test data.');
return [{ json: { subject, body: lines.join('\n') } }];
"""

node = s1.node
nodes = [
    node("Run Crashed", "n8n-nodes-base.errorTrigger", 1, [0, 0], {}),
    node("Called by Shift Desk", "n8n-nodes-base.executeWorkflowTrigger", 1.1, [0, 200],
         {"inputSource": "passthrough"}),
    node("Describe Problem", "n8n-nodes-base.code", 2, [260, 100],
         {"mode": "runOnceForAllItems", "jsCode": DESCRIBE_JS.strip()}),
    # Asks Gmail "who am I signed in as?" so the alert address never has to be written down here.
    node("Who Gets Alerts", "n8n-nodes-base.httpRequest", 4.5, [520, 100], {
        "method": "GET", "url": "https://gmail.googleapis.com/gmail/v1/users/me/profile",
        "authentication": "predefinedCredentialType", "nodeCredentialType": "gmailOAuth2",
        "options": {"timeout": 20000},
    }, credentials={"gmailOAuth2": GMAIL_CRED}, retryOnFail=True, maxTries=2, waitBetweenTries=2000),
    node("Email Alert", "n8n-nodes-base.gmail", 2.2, [780, 100], {
        "resource": "message", "operation": "send",
        "sendTo": "={{ $json.emailAddress.replace('@', '+alerts@') }}",
        "subject": "={{ $('Describe Problem').item.json.subject }}",
        "emailType": "text",
        "message": "={{ $('Describe Problem').item.json.body }}",
        "options": {"appendAttribution": False, "senderName": "Shift Desk Alerts"},
    }, credentials={"gmailOAuth2": GMAIL_CRED}),
]


def link(to):
    return [{"node": to, "type": "main", "index": 0}]


connections = {
    "Run Crashed": {"main": [link("Describe Problem")]},
    "Called by Shift Desk": {"main": [link("Describe Problem")]},
    "Describe Problem": {"main": [link("Who Gets Alerts")]},
    "Who Gets Alerts": {"main": [link("Email Alert")]},
}

workflow = {
    "name": "Shift Desk Alerts",
    "id": WORKFLOW_ID,
    "nodes": nodes,
    "connections": connections,
    "settings": {"executionOrder": "v1", "timezone": "Pacific/Honolulu", "callerPolicy": "workflowsFromSameOwner"},
    "active": False,
    "pinData": {},
}

out = HERE / "shift-desk-alerts.json"
out.write_text(json.dumps(workflow, indent=2, ensure_ascii=False))
print("wrote", out)
