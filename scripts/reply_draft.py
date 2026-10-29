#!/usr/bin/env python3
"""Save a reply DRAFT (with optional PDF attachment) into Gmail Drafts over IMAP, threaded under the newest
message from a sender. Nothing is sent; you review and sends from Gmail. Links stay exactly as written
(the Gmail connector would rewrite them into google.com/url redirects).

  python3 scripts/reply_draft.py --from-sender chloe@embercopilot.ai --body-file body.txt [--attach file.pdf]
"""
import argparse, email, email.policy, email.utils, imaplib, json, os, sys, time
from email.message import EmailMessage
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from draft_send import cfg, app_password, ctx, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--from-sender', required=True)
    ap.add_argument('--body-file', required=True)
    ap.add_argument('--attach')
    a = ap.parse_args()
    account, service = cfg()
    pw = app_password(account, service)
    M = imaplib.IMAP4_SSL('imap.gmail.com', 993, ssl_context=ctx())
    M.login(account, pw)
    del pw
    rows = M.list()[1] or []
    allbox = next((r.decode().rsplit(' "/" ', 1)[-1] for r in rows if '\\All' in r.decode()), '"[Gmail]/All Mail"')
    drafts = next((r.decode().rsplit(' "/" ', 1)[-1] for r in rows if '\\Drafts' in r.decode()), '"[Gmail]/Drafts"')
    M.select(allbox, readonly=True)
    ids = (M.search(None, 'FROM', f'"{a.from_sender}"')[1][0] or b'').split()
    if not ids:
        out('not_found', 2, detail=f'no message from {a.from_sender}')
    orig = email.message_from_bytes(M.fetch(ids[-1], '(BODY.PEEK[HEADER])')[1][0][1], policy=email.policy.default)
    msg = EmailMessage()
    msg['From'] = account
    msg['To'] = a.from_sender
    subj = str(orig['Subject'])
    msg['Subject'] = subj if subj.lower().startswith('re:') else 'Re: ' + subj
    msg['In-Reply-To'] = orig['Message-ID']
    msg['References'] = ' '.join(x for x in [str(orig['References'] or ''), str(orig['Message-ID'])] if x)
    msg['Date'] = email.utils.formatdate(localtime=True)
    msg['Message-ID'] = email.utils.make_msgid(domain='mail.gmail.com')
    msg.set_content(open(a.body_file).read())
    if a.attach:
        msg.add_attachment(open(a.attach, 'rb').read(), maintype='application', subtype='pdf', filename=os.path.basename(a.attach))
    typ, resp = M.append(drafts, '(\\Draft)', imaplib.Time2Internaldate(time.time()), msg.as_bytes())
    M.logout()
    out('drafted' if typ == 'OK' else 'error', to=a.from_sender, subject=msg['Subject'], attachment=a.attach or '')


if __name__ == '__main__':
    main()
