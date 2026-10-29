#!/usr/bin/env python3
"""Assign each submitted application an outreach tier and store it in the outreach tracker.

  python3 scripts/tier_outreach.py            # (re)tier every application, print counts
  python3 scripts/tier_outreach.py --show C   # list one tier

Tier A: email + LinkedIn. Strong fit in a priority area (settings.json outreach.tier_a_regex) or top of pay range
        >= outreach.tier_a_min_pay_usd (default $200K), and not a stretch.
Tier B: email only. Solid fit, not a stretch.
Tier R: company already rejected you (state/gmail_signals.json): no outreach.
Tier C: no recruiter outreach. Stretch application: the log notes a borderline years requirement (5+ asked),
        or a partial/weak fit. A senior title alone is Tier B.
`tier_override` on an application (set when you ask) always wins.
Reads logs/applications-log.md for each application's notes; writes `tier` into
the outreach tracker (settings.json outreach.tracker_path).
"""
import argparse, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from userconf import tracker_path
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRACKER = Path(tracker_path())
# Tier A areas and pay line come from settings.json outreach.tier_a_regex / outreach.tier_a_min_pay_usd.
_O = json.load(open(ROOT / 'config' / 'settings.json')).get('outreach') or {}
PRIORITY = re.compile(_O.get('tier_a_regex') or r'(?!x)x', re.I)
TIER_A_PAY = _O.get('tier_a_min_pay_usd') or 200000
STRETCH = re.compile(r'partial fit|weak fit|poor fit|stretch|borderline[^.\n]{0,80}(?:\b[5-9]\+?\s*(?:-\s*\d+\s*)?(?:yrs|years)|'
                     r'senior|years)|asks? (?:about )?[5-9]\+? ?(?:-\s*\d+ )?(?:yrs|years)', re.I)


def norm(s):
    return re.sub(r'[^a-z0-9]', '', s.lower())


def sections():
    out = {}
    log = (ROOT / 'logs' / 'applications-log.md').read_text()
    for sec in re.split(r'\n(?=#{2,4} )', log):
        h = sec.split('\n', 1)[0]
        if 'SUBMITTED' not in h:
            continue
        m = re.match(r'#{2,4} \[?\d{4}-\d\d-\d\d\]?\s*(.*)', h)
        if not m:
            continue
        parts = [p.strip() for p in re.split(r'\s+—\s+', m.group(1)) if p.strip() and not p.strip().startswith('SUBMITTED')]
        if len(parts) >= 2:
            out[(norm(parts[0]), norm(' — '.join(parts[1:])))] = sec
    return out


SIGNALS = ROOT / 'state' / 'gmail_signals.json'
REJECTED = {norm(r['company']) for r in json.loads(SIGNALS.read_text()).get('rejections', [])} if SIGNALS.exists() else set()


def top_pay(text):
    nums = [float(x) * (1000 if k.lower() == 'k' else 1) for x, k in re.findall(r'\$\s?(\d{2,3}(?:\.\d)?)\s?([kK])', text)]
    return max(nums) if nums else None


def tier_for(app, sec):
    if app.get('tier_override'):
        return app['tier_override'], 'override'
    text = ' '.join([app['company'], app['role'], app.get('location', ''), sec])
    if norm(app['company']) in REJECTED:
        return 'R', 'rejected: no outreach'
    if STRETCH.search(sec):
        return 'C', 'stretch (borderline years or partial fit)'
    pay = top_pay(sec)
    if re.search(r'\b(senior|sr\.?)\b', app['role'], re.I):
        return 'B', 'senior title, no stretch flags'
    if PRIORITY.search(text):
        return 'A', 'priority area'
    if pay and pay >= TIER_A_PAY:
        return 'A', f'top of range ${pay/1000:.0f}K'
    return 'B', 'solid fit'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--show', choices=['A', 'B', 'C', 'R'])
    a = ap.parse_args()
    d = json.loads(TRACKER.read_text())
    secs = sections()
    counts = {'A': 0, 'B': 0, 'C': 0, 'R': 0}
    for app in d['applications']:
        sec = secs.get((norm(app['company']), norm(app['role'])), '')
        t, why = tier_for(app, sec)
        app['tier'], app['tier_reason'] = t, why
        counts[t] += 1
        if a.show == t:
            print(f"{app['applied_date']}  {app['company']:<24} {app['role'][:60]:<60} {why}")
    TRACKER.write_text(json.dumps(d, indent=1))
    print(json.dumps(counts))


if __name__ == '__main__':
    main()
