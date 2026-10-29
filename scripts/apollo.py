#!/usr/bin/env python3
"""Apollo broker: people search + email lookup for recruiter outreach.

Reads the Apollo API key from the macOS Keychain itself (service `apollo-api-key`), the same way
scripts/code_broker.py handles the Gmail app password, so the key never appears in a transcript.
Output is JSON on stdout and never contains the key.

  python3 scripts/apollo.py health
  python3 scripts/apollo.py search --domain figma.com --titles "recruiter,talent acquisition" [--locations "United States"] [--per-page 10]
      Free (no credits). Lists id, name, title, has_email.
  python3 scripts/apollo.py contacts --domain figma.com [--max 2]
      Free. Recruiters first; if none have an email on file, falls back to product leaders.
  python3 scripts/apollo.py contacts --domain figma.com --both
      Free. Recruiters AND product leaders (head/VP/director of product, CPO), each list up to --max.
  python3 scripts/apollo.py reveal --id <apollo person id>
      Costs 1 credit. Returns name, title, work email, email_status, linkedin_url.

Store or replace the key in your own Terminal:  security add-generic-password -U -s apollo-api-key -a <you> -w
"""
import argparse, json, subprocess, sys, urllib.request, urllib.error

SERVICE = 'apollo-api-key'
BASE = 'https://api.apollo.io/api/v1'
RECRUITER_TITLES = ['recruiter', 'technical recruiter', 'talent acquisition', 'talent partner', 'recruiting']
PRODUCT_TITLES = ['head of product', 'vp of product', 'vp product', 'director of product', 'chief product officer',
                  'group product manager']


def out(obj, code=0):
    print(json.dumps(obj, indent=1))
    sys.exit(code)


def key():
    r = subprocess.run(['security', 'find-generic-password', '-s', SERVICE, '-w'], capture_output=True, text=True)
    k = r.stdout.strip()
    if not k:
        out({'status': 'setup_error', 'detail': f'no Keychain item service={SERVICE}'}, 3)
    return k


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Content-Type': 'application/json', 'Cache-Control': 'no-cache',
                                          'X-Api-Key': key()})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            detail = json.load(e)
        except Exception:
            detail = {}
        out({'status': 'api_error', 'http': e.code,
             'detail': detail.get('error') or detail.get('message') or str(e)}, 4)


def search(domain, titles, locations=None, per_page=10):
    body = {'q_organization_domains_list': [domain], 'person_titles': titles, 'per_page': per_page}
    if locations:
        body['person_locations'] = locations
    d = call('POST', '/mixed_people/api_search', body)
    people = [{'id': p.get('id'), 'first_name': p.get('first_name'),
               'last_name': p.get('last_name') or p.get('last_name_obfuscated'),
               'title': p.get('title'), 'has_email': p.get('has_email')} for p in d.get('people', [])]
    total = (d.get('pagination') or {}).get('total_entries', d.get('total_entries'))
    return people, total


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('health')
    s = sub.add_parser('search')
    s.add_argument('--domain', required=True)
    s.add_argument('--titles', required=True)
    s.add_argument('--locations', default='')
    s.add_argument('--per-page', type=int, default=10)
    c = sub.add_parser('contacts')
    c.add_argument('--domain', required=True)
    c.add_argument('--max', type=int, default=2)
    c.add_argument('--locations', default='United States')
    c.add_argument('--both', action='store_true')
    r = sub.add_parser('reveal')
    r.add_argument('--id', required=True)
    a = ap.parse_args()

    if a.cmd == 'health':
        out(call('GET', '/auth/health'))
    if a.cmd == 'search':
        people, total = search(a.domain, [t.strip() for t in a.titles.split(',') if t.strip()],
                               [l.strip() for l in a.locations.split(',') if l.strip()], a.per_page)
        out({'status': 'ok', 'total': total, 'people': people})
    if a.cmd == 'contacts':
        locs = [l.strip() for l in a.locations.split(',') if l.strip()]
        rec, _ = search(a.domain, RECRUITER_TITLES, locs, 25)
        rec = [p for p in rec if p['has_email']][:a.max]
        if a.both:
            prod, _ = search(a.domain, PRODUCT_TITLES, locs, 25)
            prod = [p for p in prod if p['has_email']][:a.max]
            out({'status': 'ok' if rec or prod else 'none_found', 'recruiters': rec, 'product_leaders': prod})
        if rec:
            out({'status': 'ok', 'contact_type': 'recruiter', 'people': rec})
        prod, _ = search(a.domain, PRODUCT_TITLES, locs, 25)
        prod = [p for p in prod if p['has_email']][:1]
        out({'status': 'ok' if prod else 'none_found', 'contact_type': 'product_leader', 'people': prod})
    if a.cmd == 'reveal':
        d = call('POST', '/people/match', {'id': a.id, 'reveal_personal_emails': False})
        p = d.get('person') or {}
        out({'status': 'ok' if p else 'not_found', 'name': p.get('name'), 'title': p.get('title'),
             'email': p.get('email'), 'email_status': p.get('email_status'),
             'linkedin_url': p.get('linkedin_url'),
             'organization': (p.get('organization') or {}).get('name')})


if __name__ == '__main__':
    main()
