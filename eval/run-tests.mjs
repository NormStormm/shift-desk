// Sends every test request to the Shift Desk workflow and scores the answers.
//
//   node eval/run-tests.mjs                 # all 40 requests
//   node eval/run-tests.mjs T03 T25         # just these
//
// Needs the workflow published in n8n (webhook: /webhook/shift-desk-test).
// Writes eval/results/<timestamp>.json and .csv, and prints a scorecard.

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const URL_ = process.env.SHIFT_DESK_URL || 'http://localhost:5678/webhook/shift-desk-test';
const CONCURRENCY = 3;
const PRICE = { 'claude-opus-5-5': [4, 20], 'claude-haiku-5-5': [0.1, 0.5], 'claude-sonnet-5-5': [2, 10] }; // $ per 1M in/out

const URGENCY_RANK = { LOW: 0, NORMAL: 1, HIGH: 2, URGENT: 3 };

// Requests where more than one answer is reasonable (documented in test-data notes).
// Entries marked F4 were added after run 1; each reason is recorded in docs/findings.md (F4)
// *before* run 2, so the answer key isn't quietly tuned to the results.
const ALSO_ACCEPT = {
  T21: { category: ['HOUSEKEEPING'] },                  // flickering light + towels
  T22: { category: ['HOUSEKEEPING', 'MAINTENANCE'], urgency: ['NORMAL', 'URGENT'],
         route: ['REVIEW'] },                           // F4: review queue = Duty Manager, whom the guest asked for
  T24: { category: ['MAINTENANCE'] },                   // F4: vague "isn't working"; what matters is route = REVIEW
  T27: { category: ['FRONT_DESK'] },                    // lost passport: security or front desk
  T31: { category: ['FRONT_DESK'] },                    // what's included in my rate
  T32: { urgency: ['NORMAL'] },                         // F4: skip service *today*
  T33: { category: ['MAINTENANCE'], urgency: ['NORMAL'] }, // ants
  T39: { urgency: ['NORMAL'] },                         // concierge booking
  T40: { urgency: ['NORMAL'] },                         // F4: guest wants it fixed during the stay
};

const cases = JSON.parse(fs.readFileSync(path.join(here, '..', 'test-data', 'requests.json'), 'utf8'));
// --rescore <results.json>: re-grade a saved run with the current answer key (no requests sent, no cost).
const rescoreIdx = process.argv.indexOf('--rescore');
const rescoreFile = rescoreIdx > -1 ? process.argv[rescoreIdx + 1] : null;
const only = rescoreFile ? [] : process.argv.slice(2);
const todo = only.length ? cases.filter((c) => only.includes(c.id)) : cases;

async function send(c) {
  const t0 = Date.now();
  try {
    const r = await fetch(URL_, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ test_id: c.id, from: c.from, room: c.room, message: c.message }),
    });
    const text = await r.text();
    let body;
    try { body = JSON.parse(text); } catch { body = { error: 'Non-JSON response: ' + text.slice(0, 200) }; }
    if (!r.ok) body = { error: `HTTP ${r.status}: ${text.slice(0, 200)}` };
    return { ...body, wall_ms: Date.now() - t0 };
  } catch (e) {
    return { error: 'Request failed: ' + e.message, wall_ms: Date.now() - t0 };
  }
}

function score(c, got) {
  const exp = c.expected;
  const alt = ALSO_ACCEPT[c.id] || {};
  const expectReview = !!exp.needs_review;
  const catOk = got.category === exp.category || (alt.category || []).includes(got.category);
  // F4: items that should go to human review don't use urgency for anything, so it isn't scored.
  const urgOk = expectReview || got.urgency === exp.urgency || (alt.urgency || []).includes(got.urgency);
  const diff = (URGENCY_RANK[got.urgency] ?? -9) - URGENCY_RANK[exp.urgency];
  const routeOk = (expectReview ? got.route === 'REVIEW' : got.route === 'AUTO') || (alt.route || []).includes(got.route);
  return {
    catOk, urgOk, routeOk,
    underPriority: !urgOk && diff < 0,
    overPriority: !urgOk && diff > 0,
    // The dangerous kind of mistake: a HIGH/URGENT request rated lower than HIGH.
    missedEmergency: !urgOk && !expectReview && URGENCY_RANK[exp.urgency] >= 2 && (URGENCY_RANK[got.urgency] ?? -9) < 2,
  };
}

async function pool(items, n, fn) {
  const out = new Array(items.length);
  let i = 0;
  await Promise.all(Array.from({ length: n }, async () => {
    while (i < items.length) { const k = i++; out[k] = await fn(items[k], k); }
  }));
  return out;
}

const saved = rescoreFile ? JSON.parse(fs.readFileSync(rescoreFile, 'utf8')) : null;
const savedGot = saved ? Object.fromEntries(saved.results.map((r) => [r.id, r.got])) : {};
console.log(rescoreFile ? `Re-scoring ${rescoreFile} with the current answer key (no requests sent)…\n`
                        : `Sending ${todo.length} requests to ${URL_} (${CONCURRENCY} at a time)…\n`);
const results = await pool(todo, CONCURRENCY, async (c) => {
  const got = rescoreFile ? (savedGot[c.id] || { error: 'missing from saved run' }) : await send(c);
  const s = got.error && !got.category ? { catOk: false, urgOk: false, routeOk: false } : score(c, got);
  const mark = (ok) => (ok ? '✓' : '✗');
  console.log(`${c.id}  cat ${mark(s.catOk)} ${String(got.category).padEnd(13)} urg ${mark(s.urgOk)} ${String(got.urgency).padEnd(7)} route ${mark(s.routeOk)} ${String(got.route).padEnd(6)} conf ${got.confidence ?? '-'}  ${got.error ? 'ERROR: ' + got.error : ''}`);
  return { id: c.id, message: c.message, expected: c.expected, note: c.note || '', got, ...s };
});

const n = results.length;
const pct = (k) => `${results.filter((r) => r[k]).length}/${n} (${Math.round((100 * results.filter((r) => r[k]).length) / n)}%)`;
const tokensIn = results.reduce((a, r) => a + (r.got.input_tokens || 0), 0);
const tokensOut = results.reduce((a, r) => a + (r.got.output_tokens || 0), 0);
const model = results.find((r) => r.got.model)?.got.model || 'unknown';
const [pi, po] = PRICE[model] || [0, 0];
const cost = (tokensIn * pi + tokensOut * po) / 1e6;
const lat = results.map((r) => r.got.latency_ms).filter(Boolean).sort((a, b) => a - b);
const median = lat.length ? lat[Math.floor(lat.length / 2)] : 0;

console.log('\n──────── Scorecard ────────');
console.log('Model:              ', model);
console.log('Category correct:   ', pct('catOk'));
console.log('Urgency correct:    ', pct('urgOk'));
console.log('Route correct:      ', pct('routeOk'), ' (AUTO vs human REVIEW)');
console.log('Missed emergencies: ', results.filter((r) => r.missedEmergency).map((r) => r.id).join(', ') || 'none', ' ← HIGH/URGENT rated lower than HIGH');
console.log('Under-prioritized:  ', results.filter((r) => r.underPriority).map((r) => r.id).join(', ') || 'none');
console.log('Over-prioritized:   ', results.filter((r) => r.overPriority).map((r) => r.id).join(', ') || 'none');
console.log('Errors:             ', results.filter((r) => r.got.error).map((r) => r.id).join(', ') || 'none');
console.log(`Median latency:      ${(median / 1000).toFixed(1)} s`);
console.log(`Tokens:              ${tokensIn} in / ${tokensOut} out  ≈ $${cost.toFixed(3)} total, $${(cost / n).toFixed(4)} per request`);

if (rescoreFile) process.exit(0);
const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
const dir = path.join(here, 'results');
fs.mkdirSync(dir, { recursive: true });
fs.writeFileSync(path.join(dir, `${stamp}.json`), JSON.stringify({ model, cost, results }, null, 2));
const csvEsc = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`;
const header = ['id', 'expected_category', 'got_category', 'cat_ok', 'expected_urgency', 'got_urgency', 'urg_ok', 'route', 'route_ok', 'confidence', 'summary', 'urgency_note', 'review_reason', 'error'];
const rows = results.map((r) => [r.id, r.expected.category, r.got.category, r.catOk, r.expected.urgency, r.got.urgency, r.urgOk,
  r.got.route, r.routeOk, r.got.confidence, r.got.summary, r.got.urgency_note, r.got.review_reason, r.got.error].map(csvEsc).join(','));
fs.writeFileSync(path.join(dir, `${stamp}.csv`), [header.join(','), ...rows].join('\n'));
console.log(`\nSaved eval/results/${stamp}.json and .csv`);
