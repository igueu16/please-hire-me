#!/usr/bin/env python3
"""Find which LinkedIn-sourced roles sit on a public ATS board an agent can apply through.

  python3 scripts/ats_probe.py IN.tsv OUT_ats.tsv OUT_portal.tsv

IN.tsv: company, title, location, area, years, pay, linkedin_url (no header).
For each company, tries Greenhouse, Ashby and Lever board slugs built from the name, then looks for a posting whose
title matches. Rows with a match go to OUT_ats.tsv with the ATS apply URL appended; the rest go to OUT_portal.tsv
(account portal or no public board: apply by hand).
"""
import concurrent.futures as cf, json, re, sys, urllib.request

UA = {'User-Agent': 'Mozilla/5.0'}
STOP = r'\b(inc|llc|ltd|corp|corporation|co|company|group|technologies|technology|labs|ai|health|the|us|usa|software|holdings|solutions)\b'


def get(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15) as r:
            return json.load(r)
    except Exception:
        return None


def slugs(name):
    n = re.sub(r'\(.*?\)|[®™,.&\']', ' ', name.lower())
    full = re.sub(r'[^a-z0-9]', '', n)
    core = re.sub(r'[^a-z0-9]', '', re.sub(STOP, ' ', n))
    first = re.sub(r'[^a-z0-9]', '', n.split()[0]) if n.split() else ''
    dashed = re.sub(r'[^a-z0-9]+', '-', n.strip()).strip('-')
    out = []
    for s in (full, core, first, dashed, full + 'inc', core + 'hq', core + 'ai'):
        if s and len(s) > 2 and s not in out:
            out.append(s)
    return out


def norm(t):
    return set(re.findall(r'[a-z]+', t.lower())) - {'senior', 'sr', 'the', 'and', 'of', 'for', 'a', 'ii', 'i', 'iii'}


def jobs_for(slug):
    found = []
    d = get(f'https://boards-api.greenhouse.io/v1/boards/{slug}/jobs')
    if d and d.get('jobs'):
        found += [(j['title'], j.get('absolute_url', ''), 'greenhouse') for j in d['jobs']]
    d = get(f'https://api.ashbyhq.com/posting-api/job-board/{slug}')
    if d and d.get('jobs'):
        found += [(j['title'], j.get('jobUrl', ''), 'ashby') for j in d['jobs']]
    d = get(f'https://api.lever.co/v0/postings/{slug}?mode=json')
    if isinstance(d, list) and d:
        found += [(j.get('text', ''), j.get('hostedUrl', ''), 'lever') for j in d]
    return found


def probe(row):
    company, title = row[0], row[1]
    want = norm(title)
    for s in slugs(company):
        jobs = jobs_for(s)
        if not jobs:
            continue
        best = max(jobs, key=lambda j: len(want & norm(j[0])) / (len(want | norm(j[0])) or 1))
        score = len(want & norm(best[0])) / (len(want | norm(best[0])) or 1)
        if score >= 0.5:
            return row + [best[1], best[2], best[0]]
        return row + ['', f'board:{s} (no matching title)', '']
    return row + ['', 'no public board', '']


def main():
    src, ats_out, portal_out = sys.argv[1:4]
    rows = [l.rstrip('\n').split('\t') for l in open(src) if l.strip()]
    with cf.ThreadPoolExecutor(8) as ex:
        res = list(ex.map(probe, rows))
    ats = [r for r in res if r[7]]
    portal = [r for r in res if not r[7]]
    open(ats_out, 'w').write('\n'.join('\t'.join(r) for r in ats) + ('\n' if ats else ''))
    open(portal_out, 'w').write('\n'.join('\t'.join(r) for r in portal) + ('\n' if portal else ''))
    print(json.dumps({'in': len(rows), 'on_public_ats': len(ats), 'portal_or_none': len(portal)}))


if __name__ == '__main__':
    main()
