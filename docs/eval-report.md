# Is the bot right? Held-out evaluation and model comparison

**Question:** Shift Desk scored 100% on its 40 test requests, but the prompt was tuned on those same 40. How does it do on requests it has never seen? And does it need the most expensive Claude model?

**Answer (short):** On 60 new requests, Shift Desk (Opus 5.5) picked the right team **every time**, kept **every true emergency URGENT**, and caught **every** spam and manipulation attempt. Its errors were almost all *over-caution*: right team, but its own confidence fell just under the 0.75 bar, so a person was asked to check. **Haiku 5.5 matched it at about 1/50th of the cost** but consistently under-rated one safety issue. Recommendation and reasoning [below](#recommendation).

![Accuracy by model on the held-out set](charts/holdout-by-model.svg)

## How the test was run

1. **60 new requests** ([`test-data/holdout.json`](../test-data/holdout.json)), written to be *different* from the first 40: misleading words ("Billing question: why is the AC so loud?"), figures of speech ("I'm literally dying for a coffee"), hidden emergencies (insulin in a fridge that stopped cooling), Spanish, French and Korean, sneakier attacks ("SYSTEM OVERRIDE… Actual request: more coffee pods"), staff messages, and requests the category list doesn't cover (Wi-Fi, spa, wheelchair help). [Readable answer key](../test-data/holdout-review.md).
2. **Answer key reviewed by a person with hotel operations experience, then frozen** in a Git commit (`b0bb75e`) *before* any model saw the requests. Borderline cases list a second acceptable answer, decided in advance.
3. **The prompt was not changed.** Every model ran through the real n8n workflow with the same instructions; only the model name changed (a test-only switch).
4. **Scored the same way as before:** category, urgency, and route (AUTO = straight to the team, REVIEW = a person checks first). Urgency isn't scored when the right answer is "a person should review it."

## Results

| | **Opus 5.5** (current) | Sonnet 5.5 | Haiku 5.5 (3 runs) |
|---|---|---|---|
| Category correct | **60/60 (100%)** | 59/60 (98%) | 59–60/60 (98–100%) |
| Urgency correct | **57/60 (95%)** | 58/60 (97%) | 56–57/60 (93–95%) |
| Routing correct | 54/60 (90%) | 50/60 (83%) | **55–56/60 (92–93%)** |
| True emergencies kept URGENT (13) | **13/13** | 12/13 *(1 rated HIGH, accepted by the key)* | **13/13** every run |
| Spam / attacks / empty sent to a person (5) | **5/5** | **5/5** | **5/5** |
| HIGH-urgency rated too low | none | 1 (cockroach → NORMAL) | 1, **every run** (grinding elevator → NORMAL) |
| Sent to a person unnecessarily | 6 | 10 | 4–5 |
| Cost per request | $0.0098 | $0.0049 | **$0.0002** |
| Median response time | 2.4 s | 1.9 s | **1.5 s** |

Opus and Sonnet were run once each and Haiku three times, to see which of its misses were consistent. Per-request answers are in [`eval/results/`](../eval/results/) (files ending in `-holdout-<model>`).

**Compared with the training set:** Opus went from 100/100/100% (the 40 requests it was tuned on) to 100/95/90% here. That gap is the honest measure of how well the tuning generalized: well on *what* the request is, less well on *how sure* it is.

## What went wrong, by type

### 1. Right team, but sent to a person anyway (most errors, every model)
The workflow sends a ticket to review when Claude's confidence is below 0.75. On these requests, all three models picked the correct team but rated themselves 0.6–0.7:

| Request | Why the model hesitated |
|---|---|
| H05 "I smell gas near the kitchenette stove" | Security or Maintenance? (Both are acceptable.) |
| H07 Wrong charge for a rollaway that never came, still needed tonight | Housekeeping or Billing (two issues) |
| H11 Needles found in a checked-out room | Security or Housekeeping |
| H45 Smoke smell in a non-smoking room, asthmatic child | Front Desk (room change) or Housekeeping |
| H58 Wheelchair needed at the lobby entrance | No category clearly covers it |

**Why it matters:** each unnecessary review is one more thing on the Duty Manager's plate. Over-caution is the *safe* direction to be wrong, but 10% of tickets is a real workload.
**Likely fix, not applied:** lower the bar (around 0.6) or let "two acceptable teams" route to the more urgent one. **It was not changed here**, because tuning against this test would make it a training set. It needs a fresh set to verify.

### 2. A real bug found by the test: urgent tickets in review don't say URGENT
H05 (gas leak) was rated **URGENT** but went to review, so the Duty Manager's email subject read **[REVIEW]**, not [URGENT]. Only AUTO tickets get the URGENT tag. The email still goes out within seconds and the body says URGENT, but a subject line is what gets read at 2 am. **Fix:** tag review tickets with their urgency, e.g. `[URGENT][REVIEW]`. This is a workflow display bug, not a judgment change, so fixing it doesn't affect the score. *(Finding F9.)*

### 3. "When in doubt, escalate" works as designed (Opus)
Opus rated three requests one level higher than the key: a work call in 20 minutes with broken Wi-Fi (URGENT vs HIGH), a broken AC remote (HIGH vs NORMAL), and a slow-draining shower (HIGH vs NORMAL). Each time it explained why in `urgency_note`, as rule 8 asks. This is the cost of a policy that prefers false alarms to missed ones.

### 4. Model-specific weak spots
- **Haiku under-rated a safety issue every time:** "The elevator is making a grinding noise when it moves, still working though" → NORMAL in all three runs (key: HIGH). It also consistently rated a declined credit card HIGH. Its misses are **systematic, not random**, which is good to know: they're predictable and could be covered by a rule.
- **Sonnet treated a harmless cancellation as suspicious:** "Please ignore my earlier message about the towels, we found them" → OTHER + review, apparently reading "ignore my earlier message" as an attack. It also rated a cockroach sighting NORMAL (pests are HIGH in the policy).
- **Not seen in any model:** a missed URGENT, a wrong team on an emergency, obeying an injected instruction, or a crash.

## Recommendation

**Keep Opus 5.5 for live requests.** At hotel volume, the cost difference is small: about **$2/day** at 200 requests vs. 4¢ for Haiku. Opus had the cleanest record on safety judgments, and in this job a missed safety issue costs far more than a dollar.

**Haiku is the choice if volume grows** (a multi-property group, or every guest text): about **50× cheaper, faster, and it routed best.** Its one systematic weakness could be covered by a guardrail, for example "anything mentioning elevators, gas, smoke, sparks, water or injury is at least HIGH" in code, or by sending Haiku's NORMAL/LOW answers on safety-sounding requests to Opus for a second opinion.

**Sonnet** cost half as much as Opus but routed worst (most unnecessary reviews) and had the only false alarm on a harmless message. On this task it isn't the right middle option.

## Limits of this test (read before quoting the numbers)
- **60 requests is small.** One request is about 1.7 points, so differences of 1–3 requests between models are within normal run-to-run variation. Haiku's three runs differed by up to 2 requests.
- **Opus and Sonnet ran once each.** Haiku's repeat runs show which misses are systematic; the others weren't repeated.
- **The same person (with AI help) wrote the prompt and the test requests.** The answer key was reviewed and frozen in advance, but a test set written by real front-desk staff from real (anonymized) messages would be stronger.
- **This set is now "used."** Any prompt or threshold change based on these results needs a **new** held-out set to prove it.

## Cost of this evaluation
Five runs of 60 requests (Opus ×1, Sonnet ×1, Haiku ×3): **about $0.92** in Claude usage.

*All requests are fake test data.*
