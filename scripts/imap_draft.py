#!/usr/bin/env python3
"""Create an outreach DRAFT in a Gmail account's Drafts over IMAP (no Gmail connector, so links stay real).
Used for the second sending account (any address in settings.json outreach_senders), which the Gmail connector cannot reach.
Nothing is sent; send later with scripts/send_batch.py --account <same account>.

  python3 scripts/imap_draft.py --account second.inbox@gmail.com --to x@co.com --subject "..." --body-file body.txt
      [--html-file body.html]

body.txt is the plain text including the signature. Without --html-file, a simple HTML part is built from it
(paragraphs, the LinkedIn URL as a link).
"""
import argparse, email.utils, html, imaplib, os, re, sys, time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from draft_send import cfg, app_password, ctx, out


def to_html(text):
    paras = [p.strip() for p in text.strip().split('\n\n')]
    body = ''.join('<p>' + '<br>'.join(html.escape(l) for l in p.split('\n')) + '</p>' for p in paras)
    return re.sub(r'(https?://[^\s<]+)', lambda m: f'<a href="{m.group(1)}">{m.group(1).split("://", 1)[1]}</a>', body)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--account', required=True)
    ap.add_argument('--to', required=True)
    ap.add_argument('--subject', required=True)
    ap.add_argument('--body-file', required=True)
    ap.add_argument('--html-file')
    a = ap.parse_args()
    account, service = cfg(a.account)
    plain = open(a.body_file).read()
    msg = MIMEMultipart('alternative')
    msg['From'], msg['To'], msg['Subject'] = account, a.to, a.subject
    msg['Date'] = email.utils.formatdate(localtime=True)
    msg['Message-ID'] = email.utils.make_msgid(domain='mail.gmail.com')
    msg.attach(MIMEText(plain, 'plain', 'utf-8'))
    msg.attach(MIMEText(open(a.html_file).read() if a.html_file else to_html(plain), 'html', 'utf-8'))
    pw = app_password(account, service)
    M = imaplib.IMAP4_SSL('imap.gmail.com', 993, ssl_context=ctx())
    M.login(account, pw)
    del pw
    rows = M.list()[1] or []
    drafts = next((r.decode().rsplit(' "/" ', 1)[-1] for r in rows if '\\Drafts' in r.decode()), '"[Gmail]/Drafts"')
    typ, _ = M.append(drafts, '(\\Draft)', imaplib.Time2Internaldate(time.time()), msg.as_bytes())
    M.logout()
    out('drafted' if typ == 'OK' else 'error', account=account, to=a.to, subject=a.subject)


if __name__ == '__main__':
    main()
