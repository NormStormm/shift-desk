# Findings log: what broke in testing, and what changed

Each entry: what happened → why → fix → how it was verified.

## F1: Form "Problem submitting response" (environment, not workflow)
- **What happened:** The first form submission showed "Problem submitting response."
- **Why:** n8n had **zero executions** recorded, so the request never arrived. The browser's network log showed the POST was blocked client-side (`ERR_BLOCKED_BY_CLIENT`) by the embedded browser it was opened in.
- **Fix:** Use a normal browser for n8n forms.
- **Lesson:** Check whether a request reached the server before debugging the workflow.

## F2: Clear tickets parked in human review over urgency doubts (design)
- **What happened:** "My AC is broken and I get overheated very easily" → MAINTENANCE at 0.93 confidence, but `needs_human_review: true` ("heat sensitivity may indicate vulnerability; consider escalating to urgent"). The ticket went to the review queue instead of Engineering.
- **Why:** The prompt used one flag for two different doubts: *which team* (a real blocker) and *how urgent* (not a blocker).
- **Fix:** Urgency doubts never block routing. Claude picks the **higher** urgency and explains it in a new `urgency_note` field ("when in doubt, escalate"). `needs_human_review` is reserved for unroutable requests (OTHER, manipulation attempts, unclear team).
- **Verified:** Same request re-run → MAINTENANCE, **URGENT**, route **AUTO**, escalate **true**, urgency_note "possible vulnerable guest, so escalated from HIGH to URGENT."

## Run 1: full 40-request test (claude-opus-5-5, before F3/F4 fixes)
Category 39/40 (98%) · Urgency 32/40 (80%) · Route 39/40 (98%) · 0 errors · median 2.2 s · $0.0088/request.
**All 20 HIGH/URGENT requests were rated at or above HIGH**, except T33 (ants), which came back NORMAL, an answer pre-marked as acceptable. Prompt injection (T25) caught and explained; the Japanese request (T23) was summarized in English; the two-issue message (T21) had both issues in the summary.

## F3: Routine requests with a future deadline rated LOW instead of NORMAL (prompt)
- **What happened:** T04 (double charge, "before I check out Friday"), T05 (late checkout tomorrow), T18 (disputed minibar charge), T36 (refund missing after 10 days) all came back **LOW**.
- **Why:** The prompt defined LOW as "requests with no time pressure." Claude read a deadline days away as no pressure, but a guest is still waiting on an answer.
- **Proposed fix:** LOW only for pure information questions or requests the guest explicitly says can wait. Anything a guest is waiting on an answer or action for during their stay is at least NORMAL.

## F4: Some "misses" were the answer key's fault, not Claude's (evaluation design)
Reviewing each miss before changing anything:
- **T25 / T37 urgency:** These go to human review, so their urgency doesn't drive anything. The grader shouldn't score urgency for review-expected items.
- **T32** ("skip our room today, DND"): Claude said NORMAL. It's about *today's* service, so NORMAL is defensible.
- **T40** ("not urgent but please fix during our stay"): NORMAL is defensible too; the guest wants it done this stay.
- **T24** ("the thing in the room isnt working"): Claude chose MAINTENANCE at 0.6 confidence **and** sent it to review. The outcome (a person checks) is exactly right.
- **T22** (angry guest demanding a manager): sent to review at 0.7 confidence, and the review queue goes to the Duty Manager, the person the guest asked for.
- **Lesson:** An answer key is a guess too. Every label change is written down here, with its reason, *before* the re-run, so the score isn't quietly tuned to pass.

## F5: First test after a deploy hit the old version (deployment race)
- **What happened:** After importing the F3 prompt fix, publishing it, and restarting n8n, a spot-check of T05 still came back LOW.
- **Why:** The execution record showed it ran workflow version `0183…`, the old one. n8n's health check returned "ready" at once, but it only finished switching the live webhook to the new published version (`9d18…`) **5 seconds later** (02:25:11 test vs. 02:25:16 switch).
- **Fix:** After a deploy, confirm *which version* answered (`execution_entity.workflowVersionId`) before trusting test results, not just that the server is up.
- **Verified:** Re-ran T05 at 02:25:37 → version `9d18…` → **NORMAL** (correct).
- **Lesson:** "The fix didn't work" and "the fix isn't deployed yet" look identical from the outside. Check the version first.

## Run 2: full 40-request test after F3 (prompt) + F4 (answer key)
| | Run 1 (original key) | Run 1 re-scored (F4 key) | **Run 2 (F3 prompt + F4 key)** |
|---|---|---|---|
| Category | 39/40 (98%) | 40/40 (100%) | **40/40 (100%)** |
| Urgency | 32/40 (80%) | 36/40 (90%) | **40/40 (100%)** |
| Route (AUTO vs REVIEW) | 39/40 (98%) | 40/40 (100%) | **40/40 (100%)** |
| Missed emergencies | none | none | **none** |
| Errors | 0 | 0 | **0** |
| Cost per request | $0.0088 | n/a | **$0.0092** |
| Median latency | 2.2 s | n/a | **2.1 s** |

Re-scoring run 1 with the corrected key splits the credit: the answer-key fixes (F4) account for 80% → 90% urgency, and the prompt fix (F3) for 90% → 100%. All 40 executions in run 2 used the new workflow version.

**Caveat:** 100% is on the same 40 requests the prompt was tuned against, so it measures "fixed what we saw," not "works on everything." Next step (Project 5): a fresh held-out set the prompt has never seen, plus a cheaper-model comparison.

## F6: Same ticket logged twice in the Tickets sheet (retry + non-repeatable write)
- **What happened:** The first Stage 2 test (ticket `SD-261015-65LM`) appeared **twice** in the Tickets tab. n8n's record showed one execution, one run of the sheet step, and one row written.
- **Why (most likely):** The sheet step had "retry on fail" turned on and used plain **append**. That step took 7.8 s (reading the Teams tab took 1.9 s). This fits a pattern where Google saved the row, the reply was slow or lost, and n8n retried, so the row was saved again. n8n doesn't record retries inside a step, so this is inferred, not proven. The same reasoning had already been applied to the Gmail step (no retry, to avoid double emails) but **not** to the sheet.
- **Fix:** Write the row with **append or update, matched on `ticket_id`**. Running it twice now rewrites the same row instead of adding a copy, so retrying is safe. (This property is called *idempotency*.)
- **Verified:** Re-deployed (version `2072…`, checked per F5) and sent T01 again → ticket `SD-261015-NBS4` appears **once**.
- **Lesson:** Retries are only safe on steps that can run twice without harm. Before turning on "retry on fail," ask: *if this ran twice, what would happen?* For writes, give every record a unique ID and match on it.

## Stage 2 end-to-end check: form → Claude → sheet → email
A real form submission ("water leaking from the ceiling onto the bed", room 905) → `SD-261015-OHU4`: MAINTENANCE, URGENT, AUTO, escalate. Logged once to the Tickets tab, and the email reached the Engineering inbox with the Duty Manager copied (Gmail step 1.7 s). Ran on version `2072…`.

## Alerts: two kinds of failure need two paths (design)
- **Crashes:** the whole run fails (the error is never caught). n8n's **error workflow** setting starts "Shift Desk Alerts" through its Error Trigger.
- **Handled failures:** the Claude call fails, the team email fails, or the sheet log fails. By design these *don't* crash the run: the ticket still goes to review and the guest still gets an answer. That also means n8n's error workflow **never hears about them**. A new "Check for Problems" step collects them and calls the same alerts workflow directly.
- The alert goes to whichever Gmail account the credential is signed in to (looked up at run time, with `+alerts` added), so no address is written into the repo.
- Test runs don't send alerts (the 40-request eval shouldn't flood the inbox) unless they set `simulate_failure`, a test-only switch: `crash` throws in the first step, and `claude_error` requests a model that doesn't exist.
- **Known limit:** alerts are sent through Gmail. If the Google sign-in itself expires (Testing mode: every 7 days), the alert can't be sent either; the failure only shows in n8n's Executions list. A second channel (SMS/Slack) would close this gap.

## F7: False "ticket not logged" alert (my own bug, caught by the first failure test)
- **What happened:** The simulated Claude failure correctly alerted "Claude step failed," but it **also** claimed "Ticket NOT logged to the sheet." The row was in fact logged.
- **Why:** On success, the sheet step returns the row it wrote, and that row has its own `error` column (holding Claude's error). The check treated "has an `error` field" as "the write failed." Two different meanings shared one field name.
- **Fix:** A failed write returns no `ticket_id`, so check for that instead.
- **Verified:** (version `3bae…`) A simulated Claude failure → one problem reported, `logged: true`, alert sent. Simulated crash → run marked error, HTTP 500 to the caller, and the alerts workflow ran from its Error Trigger. A normal request → no problems, no alert.
- **Lesson:** Test the alarm by deliberately breaking things. An alert path that's never been triggered is an untested guess, and this one sent a false alarm on its very first real run.
- **Delivery confirmed:** all three alert emails (1 crash, 2 handled failures) arrived in the inbox.

## Shift summary: code counts, Claude writes the note (design)
- At 3 pm and 11 pm (Hawaii time), "Shift Desk Shift Summary" reads the Tickets tab, keeps the last 8 hours, and emails the Duty Manager.
- **Code does the counting** (by team, urgency and route; urgent/high list; review list; undelivered emails; repeat rooms), so the numbers are exact. **Claude only writes the 3–6 bullet handover note** on top. If the Claude call fails, the email still goes out with all the numbers and a line saying the note is unavailable.
- Claude sees ticket *summaries*, not raw guest messages, wrapped as data, with the instruction to never claim anything was resolved (the sheet doesn't track status).
- Scheduled summaries skip test runs. The on-demand test webhook (`/webhook/shift-summary-test`) can include them.
- First test (24 h, tests included): 5 tickets, an accurate note that flagged the repeated Room 1412 tickets, ~$0.011 per summary.

## F8: Review tickets showed a blank summary in the shift email
- **What happened:** The two tickets where Claude failed (simulated) appeared as "Duty Manager:" followed by nothing.
- **Why:** When the Claude step fails, there is no summary. Those are exactly the tickets a person most needs to read.
- **Fix:** Fall back to the first 70 characters of the guest's own message, marked "(no summary)".
- **Verified:** (version `99a4…`) Both lines now show the guest's words, and Claude's note flags them for follow-up.
