#!/usr/bin/env python3
"""Send a user-approved outreach batch through scripts/draft_send.py and mark each email sent in the tracker.

  python3 scripts/send_batch.py state/send-batch-<date>.txt [--gap 45] [--account second.inbox@gmail.com]

One recipient address per line, sent in file order with --gap seconds between sends. Stops at the first error
other than "already sent". Each success sets email_sent, email_sent_date, sent_via "smtp" on the matching
outreach entry in the tracker (settings.json outreach.tracker_path).
"""
import argparse, datetime, json, os, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from userconf import tracker_path

R = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
TRACKER = tracker_path()


def mark_sent(addr, account):
    d = json.load(open(TRACKER))
    for a in d['applications']:
        for o in a.get('outreach') or []:
            if (o.get('email') or '').lower() == addr.lower() and not o.get('email_sent'):
                o['email_sent'], o['email_sent_date'], o['sent_via'] = True, datetime.date.today().isoformat(), 'smtp'
                o['sent_from'] = account
    json.dump(d, open(TRACKER, 'w'), indent=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('batch')
    ap.add_argument('--gap', type=int, default=45)
    ap.add_argument('--account', help='sending Gmail account (default: broker.gmail_account)')
    a = ap.parse_args()
    addrs = [l.strip() for l in open(a.batch) if l.strip() and not l.startswith('#')]
    sent, skipped = [], []
    for i, addr in enumerate(addrs):
        r = subprocess.run([sys.executable, os.path.join(R, 'scripts', 'draft_send.py'), 'send', '--to', addr] + (['--account', a.account] if a.account else []),
                           capture_output=True, text=True)
        try:
            res = json.loads(r.stdout)
        except Exception:
            res = {'status': 'error', 'detail': (r.stderr or r.stdout)[-300:]}
        if res.get('status') == 'sent':
            mark_sent(addr, a.account)
            sent.append(addr)
            print(f"{datetime.datetime.now():%H:%M:%S} sent {addr}", flush=True)
        elif res.get('status') == 'refused' and 'already' in res.get('detail', ''):
            skipped.append(addr)
            print(f"skip (already sent) {addr}", flush=True)
        else:
            print(json.dumps({'stopped_at': addr, 'result': res, 'sent': len(sent)}), flush=True)
            sys.exit(1)
        if i < len(addrs) - 1:
            time.sleep(a.gap)
    print(json.dumps({'sent': len(sent), 'skipped': skipped}), flush=True)


if __name__ == '__main__':
    main()
