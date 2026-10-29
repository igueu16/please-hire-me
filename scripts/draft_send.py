#!/usr/bin/env python3
"""Send an existing Gmail draft over SMTP with its links restored, so recipients get the real URL.

The Gmail connector rewrites every link in a draft into a https://www.google.com/url?q=... redirect, and the
redirect is what recipients receive. This script finds the draft over IMAP, puts the original URLs back in both
the plain-text and HTML parts, and sends that exact text through smtp.gmail.com. Gmail files the sent copy in Sent.
The draft itself is left in Drafts (delete it by hand once sent).

  python3 scripts/draft_send.py preview --to someone@company.com
      Prints the subject and fixed plain text. Sends nothing.
  python3 scripts/draft_send.py send --to someone@company.com [--test-to me@gmail.com]
      Sends the draft addressed to --to. With --test-to, sends it to that address instead, subject prefixed [TEST].
  --account second.inbox@gmail.com works with either command: that account's Drafts and SMTP login
      (Keychain service from settings.json outreach_senders).

Uses the same Gmail app password as scripts/code_broker.py (Keychain item from settings.json -> broker), read here
and never printed. Refuses to send to an address already in logs/draft-send.log, or if any redirect link remains.
Only user-approved batches are sent with this.
"""
import argparse, datetime, email, email.policy, email.utils, imaplib, json, os, re, smtplib, ssl, subprocess, sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import unquote

R = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
LOG = os.path.join(R, 'logs', 'draft-send.log')
REDIRECT = re.compile(r'https?://www\.google\.com/url\?q=([^&"\s<>]+)[^"\s<>]*')


def out(status, code=0, **kw):
    print(json.dumps(dict(status=status, **kw), indent=1))
    sys.exit(code)


def cfg(account=None):
    s = json.load(open(os.path.join(R, 'config', 'settings.json')))
    b = s.get('broker', {})
    if account and account != b.get('gmail_account'):
        service = (s.get('outreach_senders') or {}).get(account)
        if not service:
            out('setup_error', 3, detail=f'{account} is not in settings.json outreach_senders')
        return account, service
    return b.get('gmail_account'), b.get('keychain_service') or 'jobapply-gmail-imap'


def app_password(account, service):
    r = subprocess.run(['security', 'find-generic-password', '-s', service, '-a', account, '-w'], capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        out('setup_error', 3, detail=f'no Keychain item service={service}')
    return r.stdout.strip()


def ctx():
    return ssl.create_default_context(cafile='/etc/ssl/cert.pem') if os.path.exists('/etc/ssl/cert.pem') else ssl.create_default_context()


def unwrap(text):
    return REDIRECT.sub(lambda m: unquote(m.group(1)), text)


def find_draft(account, pw, to):
    M = imaplib.IMAP4_SSL('imap.gmail.com', 993, ssl_context=ctx())
    M.login(account, pw)
    typ, rows = M.list()
    box = next((r.decode().rsplit(' "/" ', 1)[-1] for r in rows or [] if '\\Drafts' in r.decode()), '"[Gmail]/Drafts"')
    M.select(box, readonly=True)
    typ, data = M.search(None, 'TO', f'"{to}"')
    ids = data[0].split() if data and data[0] else []
    # Gmail's IMAP TO search is fuzzy (it matched other drafts on 2026-10-27), so keep only drafts whose
    # To header holds exactly this address.
    exact = []
    for i in ids:
        h = email.message_from_bytes(M.fetch(i, '(BODY.PEEK[HEADER.FIELDS (TO)])')[1][0][1], policy=email.policy.default)
        if to.lower() in [a.lower() for a in re.findall(r"[\w.+'-]+@[\w.-]+", str(h['To'] or ''))]:
            exact.append(i)
    ids = exact
    if not ids:
        M.logout()
        out('not_found', 2, detail=f'no draft addressed to {to}')
    if len(ids) > 1:
        M.logout()
        out('ambiguous', 2, detail=f'{len(ids)} drafts addressed to {to}; resolve by hand')
    typ, msg = M.fetch(ids[0], '(RFC822)')
    M.logout()
    return email.message_from_bytes(msg[0][1], policy=email.policy.default)


def rebuild(draft, account, to_override=None):
    plain = html = None
    for part in draft.walk():
        if part.get_content_type() == 'text/plain' and plain is None:
            plain = part.get_content()
        elif part.get_content_type() == 'text/html' and html is None:
            html = part.get_content()
    plain, html = unwrap(plain or ''), unwrap(html or '')
    if 'google.com/url' in plain + html:
        out('refused', 4, detail='a redirect link is still present after unwrapping')
    msg = MIMEMultipart('alternative')
    msg['From'] = draft['From'] or account
    msg['To'] = to_override or draft['To']
    if draft['Cc'] and not to_override:
        msg['Cc'] = draft['Cc']
    msg['Subject'] = ('[TEST] ' if to_override else '') + str(draft['Subject'])
    msg['Date'] = email.utils.formatdate(localtime=True)
    msg['Message-ID'] = email.utils.make_msgid(domain='mail.gmail.com')
    msg.attach(MIMEText(plain, 'plain', 'utf-8'))
    if html:
        msg.attach(MIMEText(html, 'html', 'utf-8'))
    return msg, plain


def already_sent(to):
    if not os.path.exists(LOG):
        return False
    return any(json.loads(l).get('to', '').lower() == to.lower() and not json.loads(l).get('test') for l in open(LOG) if l.strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['preview', 'send'])
    ap.add_argument('--to', required=True)
    ap.add_argument('--test-to')
    ap.add_argument('--account', help='sending Gmail account (default: broker.gmail_account)')
    a = ap.parse_args()
    account, service = cfg(a.account)
    pw = app_password(account, service)
    draft = find_draft(account, pw, a.to)
    msg, plain = rebuild(draft, account, a.test_to)
    if a.cmd == 'preview':
        out('ok', subject=msg['Subject'], to=msg['To'], plain=plain)
    if not a.test_to and already_sent(a.to):
        out('refused', 4, detail=f'{a.to} is already in {LOG}')
    rcpts = [x for _, x in email.utils.getaddresses([msg['To'] or '', msg['Cc'] or '']) if x]
    with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=ctx()) as s:
        s.login(account, pw)
        s.send_message(msg, from_addr=account, to_addrs=rcpts)
    del pw
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    rec = {'ts': datetime.datetime.now().isoformat(timespec='seconds'), 'to': a.to, 'from': account, 'sent_to': rcpts,
           'subject': msg['Subject'], 'message_id': msg['Message-ID'], 'test': bool(a.test_to)}
    with open(LOG, 'a') as f:
        f.write(json.dumps(rec) + '\n')
    out('sent', **rec)


if __name__ == '__main__':
    main()
