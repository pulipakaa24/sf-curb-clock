// Rule engine for the Sunset 72-hour parking map.
// Shared by the browser map (inlined into the HTML) and the Node test runner.
//
// All times are naive local wall-clock times stored as milliseconds of a UTC Date,
// so DST never shifts anything. A window that crosses a DST change is off by an hour;
// the UI warns about it.
(function (root) {
  'use strict';
  var HOUR = 3600e3, DAY = 24 * HOUR;

  // Day tokens used by the regulations dataset. "S" alone only appears as the end of
  // "M-S"; we read it as Saturday (Mon-Sat), which is the conservative reading of
  // a sign that most likely says Mon-Sat.
  var DAY_TOKENS = { su: 0, m: 1, mo: 1, tu: 2, t: 2, w: 3, we: 3, th: 4, f: 5, fr: 5, sa: 6, s: 6 };

  function dayIndex(tok) {
    var t = String(tok).trim().toLowerCase().replace(/[^a-z]/g, '');
    if (t in DAY_TOKENS) return DAY_TOKENS[t];
    if (t.slice(0, 2) in DAY_TOKENS) return DAY_TOKENS[t.slice(0, 2)];
    return null;
  }

  // "M-F", "M-Sa", "M-Su", "Sa,Su", "M-F, Sa" ... null/empty means every day.
  function parseDays(s) {
    var set = [false, false, false, false, false, false, false];
    if (s == null || String(s).trim() === '') return set.map(function () { return true; });
    String(s).split(/[,;&]/).forEach(function (part) {
      var ends = part.split('-');
      var a = dayIndex(ends[0]);
      if (a == null) return;
      if (ends.length < 2) { set[a] = true; return; }
      var b = dayIndex(ends[1]);
      if (b == null) { set[a] = true; return; }
      for (var i = a; ; i = (i + 1) % 7) { set[i] = true; if (i === b) break; }
    });
    if (!set.some(Boolean)) return set.map(function () { return true; }); // unparseable: assume every day
    return set;
  }

  // HHMM integer ("900", "1800", "2400") -> minutes after midnight.
  function hhmm(v) {
    if (v == null || v === '') return null;
    var n = parseInt(v, 10);
    if (isNaN(n)) return null;
    return Math.floor(n / 100) * 60 + (n % 100);
  }

  // hrs_begin/hrs_end, falling back to the "hours" text ("800-1800", "0900 - 2000").
  function beginEnd(reg) {
    var b = hhmm(reg.hrs_begin), e = hhmm(reg.hrs_end);
    if ((b == null || e == null) && reg.hours) {
      var m = /^\s*(\d{1,4})\s*-\s*(\d{1,4})\s*$/.exec(reg.hours);
      if (m) { b = hhmm(m[1]); e = hhmm(m[2]); }
    }
    return [b, e];
  }

  // Active intervals [start, end] in ms for a regulation between dayFrom and dayTo
  // (both midnight ms). begin >= end wraps past midnight; null or 0/0 means all day.
  function regIntervals(reg, dayFrom, dayTo) {
    var days = parseDays(reg.days);
    var be = beginEnd(reg), b = be[0], e = be[1];
    if (b == null || e == null || (b === 0 && e === 0)) { b = 0; e = 1440; }
    if (e <= b) e += 1440;
    var out = [];
    for (var d = dayFrom; d <= dayTo; d += DAY) {
      if (!days[new Date(d).getUTCDay()]) continue;
      out.push([d + b * 60e3, d + e * 60e3]);
    }
    return mergeIntervals(out);
  }

  function mergeIntervals(iv) {
    iv.sort(function (x, y) { return x[0] - y[0]; });
    var out = [];
    iv.forEach(function (x) {
      var last = out[out.length - 1];
      if (last && x[0] <= last[1]) last[1] = Math.max(last[1], x[1]);
      else out.push([x[0], x[1]]);
    });
    return out;
  }

  // Sweeping row: {wd: 0-6 (Sun=0), from: hour, to: hour, weeks: [w1..w5] booleans}
  function sweepIntervals(row, dayFrom, dayTo) {
    var out = [];
    var to = row.to <= row.from ? row.to + 24 : row.to;
    for (var d = dayFrom; d <= dayTo; d += DAY) {
      var dt = new Date(d);
      if (dt.getUTCDay() !== row.wd) continue;
      var wk = Math.ceil(dt.getUTCDate() / 7); // 1..5
      if (!row.weeks[wk - 1]) continue;
      out.push([d + row.from * HOUR, d + to * HOUR]);
    }
    return out;
  }

  var WD_NAMES = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  var ORD = ['1st', '2nd', '3rd', '4th', '5th'];

  function fmtHour(h) {
    h = ((h % 24) + 24) % 24;
    if (h === 0) return '12 AM';
    if (h === 12) return 'noon';
    return h < 12 ? h + ' AM' : (h - 12) + ' PM';
  }

  function describeSweep(row) {
    var w = row.weeks.every(Boolean) ? 'every' :
      row.weeks.map(function (on, i) { return on ? ORD[i] : null; }).filter(Boolean).join(' & ');
    return w + ' ' + WD_NAMES[row.wd] + ' ' + fmtHour(row.from) + '–' + fmtHour(row.to);
  }

  // For display only: combine rows that differ only in week flags (1st/3rd/5th + 2nd/4th = every).
  function mergeSweeps(rows) {
    var out = [];
    (rows || []).forEach(function (r) {
      var m = out.filter(function (o) { return o.wd === r.wd && o.from === r.from && o.to === r.to; })[0];
      if (m) m.weeks = m.weeks.map(function (on, i) { return on || r.weeks[i]; });
      else out.push({ wd: r.wd, from: r.from, to: r.to, weeks: r.weeks.slice() });
    });
    return out.sort(function (a, b) { return ((a.wd + 6) % 7) - ((b.wd + 6) % 7) || a.from - b.from; });
  }

  function regKind(reg) {
    var r = String(reg.regulation || '').toLowerCase();
    if (r.indexOf('time') === 0) return 'time';
    if (r.indexOf('no parking any') === 0) return 'never';
    if (r.indexOf('no oversized') === 0) return 'oversized';
    if (r === 'metered') return 'metered';
    if (r.indexOf('pay') === 0 || r.indexOf('paid') === 0) return 'pay';
    if (r.indexOf('rpp') === 0 || r.indexOf('residential') === 0) return 'time'; // RPP with hrlimit
    if (r === '' || r === 'unregulated') return 'none';
    return 'window'; // limited no parking, no overnight, government permit, pay or permit, unknown
  }

  function regHourLimit(reg) {
    var n = parseFloat(reg.hrlimit);
    if (!isNaN(n) && n > 0) return n;
    var m = /(\d+(?:\.\d+)?)\s*hour/i.exec(reg.regdetails || '');
    return m ? parseFloat(m[1]) : 0; // 0 => treat as no parking during the window
  }

  function fmtHHMM(v) {
    var m = hhmm(v);
    if (m == null) return null;
    var h = Math.floor(m / 60) % 24, mm = m % 60;
    var s = h === 0 ? '12' : h > 12 ? String(h - 12) : String(h);
    return s + (mm ? ':' + String(mm).padStart(2, '0') : '') + (h < 12 ? ' AM' : ' PM');
  }

  function describeHours(reg) {
    var be = beginEnd(reg);
    if (be[0] == null || be[1] == null || be[0] % 1440 === be[1] % 1440) return 'all day';
    return fmtHHMM(String(Math.floor(be[0] / 60) * 100 + be[0] % 60)) + '–' + fmtHHMM(String(Math.floor(be[1] / 60) * 100 + be[1] % 60));
  }

  function describeReg(reg) {
    var k = regKind(reg), hrs = describeHours(reg), days = reg.days || 'every day';
    if (k === 'time') {
      var lim = regHourLimit(reg);
      var rpp = (reg.rpp || []).length ? ' (RPP ' + reg.rpp.join('/') + ' exempt)' : '';
      return (lim ? lim + '-hr limit' : 'Time limited') + ', ' + days + ' ' + hrs + rpp;
    }
    if (k === 'oversized') return 'No oversized vehicles, ' + days + ' ' + hrs;
    if (k === 'never') return 'No parking any time';
    if (k === 'metered') return 'Metered curb (meter hours assumed daily 9 AM–6 PM; check the meter)';
    if (k === 'pay') return 'Pay or RPP permit' + ((reg.rpp || []).length ? ' (RPP ' + reg.rpp.join('/') + ' exempt)' : '') + ', ' + days + ' ' + hrs;
    return (reg.regulation || 'Regulation') + ', ' + days + ' ' + hrs;
  }

  // side: {sweeps: [...], regs: [reg objects]}
  // opts: {arrival: ms, hours: H, oversized: bool, rpp: 'J' | null}
  // Returns {stay: hours (<= H), end: ms|null, reason: string|null, kind}
  function evaluate(side, opts) {
    var arrival = opts.arrival, H = opts.hours, limit = arrival + H * HOUR;
    var dayFrom = Math.floor(arrival / DAY) * DAY - DAY;
    var dayTo = Math.floor(limit / DAY) * DAY + DAY;
    var best = { t: Infinity, reason: null, kind: null };
    function hit(t, reason, kind) {
      if (t < arrival) t = arrival;
      if (t < limit && t < best.t) best = { t: t, reason: reason, kind: kind };
    }

    (side.sweeps || []).forEach(function (row) {
      sweepIntervals(row, dayFrom, dayTo).forEach(function (iv) {
        if (iv[1] > arrival) hit(iv[0], 'Street sweeping (' + describeSweep(row) + ')', 'sweep');
      });
    });

    (side.regs || []).forEach(function (reg) {
      var k = regKind(reg);
      if (k === 'none') return;
      if (k === 'never') { hit(arrival, 'No parking any time', 'never'); return; }
      if (k === 'oversized' && !opts.oversized) return;
      if (k === 'pay' && opts.rpp && (reg.rpp || []).indexOf(opts.rpp) >= 0) return; // permit holders don't pay
      var ivs = regIntervals(reg, dayFrom, dayTo);
      if (k === 'time') {
        if (opts.rpp && (reg.rpp || []).indexOf(opts.rpp) >= 0) return; // permit holders exempt
        var lim = regHourLimit(reg) * HOUR;
        ivs.forEach(function (iv) {
          if (iv[1] <= arrival) return;
          var clock = Math.max(arrival, iv[0]);
          if (lim === 0) hit(clock, describeReg(reg), 'time');
          else if (iv[1] - clock > lim) hit(clock + lim, describeReg(reg) + ': limit reached', 'time');
        });
        return;
      }
      ivs.forEach(function (iv) {
        if (iv[1] > arrival) hit(iv[0], describeReg(reg), k);
      });
    });

    if (best.t === Infinity) return { stay: H, end: null, reason: null, kind: null };
    return { stay: (best.t - arrival) / HOUR, end: best.t, reason: best.reason, kind: best.kind };
  }

  function band(stay, H) {
    if (stay >= H) return 'full';
    if (stay <= 0) return 'none';
    if (stay >= H / 2) return 'half';
    if (stay >= H / 6) return 'sixth';
    return 'some';
  }

  // Meters: the current meter list has no schedules (DataSF's schedule table is from 2014),
  // so a curb with any active meter gets this assumed rule.
  var METERED = { regulation: 'Metered', days: 'M-Su', hrs_begin: '900', hrs_end: '1800', rpp: [] };

  var api = {
    METERED: METERED,
    parseDays: parseDays, hhmm: hhmm, regIntervals: regIntervals, sweepIntervals: sweepIntervals,
    evaluate: evaluate, band: band, describeSweep: describeSweep, mergeSweeps: mergeSweeps, describeReg: describeReg,
    regKind: regKind, WD_NAMES: WD_NAMES, HOUR: HOUR, DAY: DAY
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.ParkEngine = api;
})(this);
