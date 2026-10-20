// Test cases from the brief. Run: node test_engine.js
const E = require('./engine.js');
const arrival = Date.UTC(2026, 9, 18, 20, 0); // Sun 2026-10-18 20:00 (naive local)
const base = { arrival, hours: 72, oversized: false, rpp: null };
const all = [true, true, true, true, true];
const only = (i) => all.map((_, j) => j === i - 1);

const timeLimit = { regulation: 'Time limited', days: 'M-F', hours: '800-1800', hrs_begin: '800', hrs_end: '1800', hrlimit: '2', rpp: ['J'] };
const oversized = { regulation: 'No oversized vehicles', days: 'M-Su', hours: '2400-600', hrs_begin: '2400', hrs_end: '600', hrlimit: '0' };

const cases = [
  ['Time limited 2h M-F 8-6', { regs: [timeLimit] }, base, 14],
  ['Sweeping Tue 8-10 all weeks', { sweeps: [{ wd: 2, from: 8, to: 10, weeks: all }] }, base, 36],
  ['Sweeping Tue week1 only', { sweeps: [{ wd: 2, from: 8, to: 10, weeks: only(1) }] }, base, 72],
  ['Sweeping Tue week3 on', { sweeps: [{ wd: 2, from: 8, to: 10, weeks: only(3) }] }, base, 36],
  ['No oversized, vehicle oversized', { regs: [oversized] }, { ...base, oversized: true }, 4],
  ['No oversized, vehicle not oversized', { regs: [oversized] }, base, 72],
  ['No parking any time', { regs: [{ regulation: 'No parking any time' }] }, base, 0],
  // Extra checks beyond the brief
  ['RPP J holder exempt from 2h limit', { regs: [timeLimit] }, { ...base, rpp: 'J' }, 72],
  ['RPP O holder not exempt in area J', { regs: [timeLimit] }, { ...base, rpp: 'O' }, 14],
  ['4h limit every day all day (null days/hours)', { regs: [{ regulation: 'Time Limited', days: null, hours: null, hrs_begin: null, hrs_end: null, hrlimit: null, regdetails: '4 Hour Time Limit' }] }, base, 4],
  ['Arrive mid-sweep: Tue 9:00, sweep Tue 8-10', { sweeps: [{ wd: 2, from: 8, to: 10, weeks: all }] }, { ...base, arrival: Date.UTC(2026, 9, 20, 9) }, 0],
  ['Arrive right after sweep: Tue 10:00', { sweeps: [{ wd: 2, from: 8, to: 10, weeks: all }] }, { ...base, arrival: Date.UTC(2026, 9, 20, 10) }, 72],
  ['M-S parsed as Mon-Sat', { regs: [{ ...timeLimit, days: 'M-S', rpp: [] }] }, { ...base, arrival: Date.UTC(2026, 9, 24, 7) }, 3],
  ['Hours only in text field', { regs: [{ regulation: 'Time limited', days: 'M-F', hours: '0900 - 2000', hrlimit: '2' }] }, base, 15],
  ['Pay or Permit, no permit', { regs: [{ regulation: 'Pay or Permit', days: 'M-Sa', hours: '900-2100', hrs_begin: '900', hrs_end: '2100', hrlimit: '72', rpp: ['S'] }] }, base, 13],
  ['Pay or Permit, area S permit', { regs: [{ regulation: 'Pay or Permit', days: 'M-Sa', hours: '900-2100', hrs_begin: '900', hrs_end: '2100', hrlimit: '72', rpp: ['S'] }] }, { ...base, rpp: 'S' }, 72],
  ['Metered curb (assumed daily 9-6)', { regs: [E.METERED] }, base, 13],
  ['No overnight 2200-600, null days', { regs: [{ regulation: 'No overnight parking', days: null, hours: '2200-600', hrs_begin: '2200', hrs_end: '600', hrlimit: '0' }] }, base, 2],
  ['Days list M, TH', { regs: [{ regulation: 'No overnight parking', days: 'M, TH', hours: '2400-400', hrs_begin: '2400', hrs_end: '400' }] }, base, 28],
  ['Stay length 24h, sweep at 36h', { sweeps: [{ wd: 2, from: 8, to: 10, weeks: all }] }, { ...base, hours: 24 }, 24],
];

let fail = 0;
for (const [name, side, opts, want] of cases) {
  const r = E.evaluate(side, opts);
  const ok = Math.abs(r.stay - want) < 1e-9;
  if (!ok) fail++;
  const end = r.end ? new Date(r.end).toISOString().slice(0, 16).replace('T', ' ') : '—';
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}: ${r.stay} h (want ${want}), ends ${end}${r.reason ? ' — ' + r.reason : ''}`);
}
console.log(fail ? `${fail} failed` : 'all passed');
process.exit(fail ? 1 : 0);
