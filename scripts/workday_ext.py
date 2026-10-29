#!/usr/bin/env python3
"""Data for the Workday Autofill extension (extension/workday-autofill/).

    python3 scripts/workday_ext.py build          # profile.json + base resume from config/ and data/
    python3 scripts/workday_ext.py add <url>      # tailor a resume for a Workday posting and register it
    python3 scripts/workday_ext.py list           # show registered resumes
    python3 scripts/workday_ext.py import [file]  # merge answers learned in the browser (popup export,
                                                  # default ~/Downloads/workday-learned.json) into data/learned.json

`add` fetches the posting from Workday's public job API (no login), runs scripts/tailor.py, copies the
PDF into the extension and into documents.export_dir (default ~/Desktop/custom resume/), and maps the posting's job id to it so the
extension uploads the right file. Reload the extension in chrome://extensions after build/add.
Everything under extension/workday-autofill/data/ is personal and gitignored.
"""
import html, json, os, re, shutil, subprocess, sys, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from userconf import export_dir, full_name, resume_filename

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXT = os.path.join(R, 'extension', 'workday-autofill', 'data')
DESK = export_dir()
MONTHS = {m: i for i, m in enumerate('january february march april may june july august september october november december'.split(), 1)}
STATES = {'CA': 'California', 'NY': 'New York', 'MA': 'Massachusetts', 'WA': 'Washington', 'TX': 'Texas'}


def latex_text(s):
    s = s.replace('\\%', '%').replace('\\&', '&').replace('--', '-')
    return re.sub(r'\\[a-zA-Z]+\s*', '', s).replace('{', '').replace('}', '').strip()


def experience():
    tex = open(os.path.join(R, 'data/resume.tex')).read()
    out = []
    for m in re.finditer(r"\\resumeSubheading\s*\{(.*?)\}\{(.*?)\}\s*\{(.*?)\}\{(.*?)\}\s*\\resumeItemListStart(.*?)\\resumeItemListEnd", tex, re.S):
        company, dates, title, loc, body = m.groups()
        bullets = [latex_text(b) for b in re.findall(r"\\resumeItem\{(.*?)\}\n", body, re.S)]
        a, _, b = [x.strip() for x in latex_text(dates).partition('-')]
        sm, sy = a.split()
        job = dict(company=latex_text(company), title=latex_text(title), location=latex_text(loc),
                   start_month=MONTHS[sm.lower()], start_year=int(sy), current=b.lower() == 'present',
                   description='\n'.join('- ' + x for x in bullets))
        if not job['current']:
            em, ey = b.split()
            job.update(end_month=MONTHS[em.lower()], end_year=int(ey))
        out.append(job)
    return out


def skills():
    tex = open(os.path.join(R, 'data/resume.tex')).read()
    line = re.search(r"Languages .*?\\item\{(.*?)\}\}", tex, re.S).group(1)
    return [latex_text(s) for s in re.split(r"\$\\cdot\$", line) if latex_text(s)]


def skills_for(desc):
    # Resume tools only; the ones the posting names come first (never adds a skill not on the resume).
    d = desc.lower()
    return sorted(skills(), key=lambda s: (s.lower() not in d, skills().index(s)))


def yes(s):
    s = str(s or '').strip().lower()
    return True if s.startswith(('yes', 'true')) else False if s.startswith(('no', 'false')) else None


def answers(p):
    # Yes/No facts for the Application Questions step, from profile.json. None leaves the question for you.
    auth, sponsor = yes(p.get('work_authorized_us')), yes(p.get('visa_needs_sponsorship'))
    cit = str(p.get('citizenship') or '').strip().lower()
    citizen = True if cit in ('united states', 'us', 'usa', 'u.s.') else (False if cit else None)
    return dict(authorized=auth, sponsorship=sponsor, citizen=citizen,
                unrestricted=None if auth is None or sponsor is None else (auth and not sponsor),
                relocate=yes(p.get('willing_to_relocate_answer')))


def eeo(e):
    # Regexes matched against Workday's option text. A declined or missing answer picks the decline option.
    decline = 'decline|prefer not|do not wish|not wish to|choose not'
    def rx(v):
        v = str(v or '').strip()
        return decline if not v or re.search(decline, v, re.I) else '^' + re.escape(v).replace('\\ ', '\\s+')
    race = str(e.get('race') or '')
    hisp = None if not race or re.search(decline, race, re.I) else bool(re.search('hispanic|latin', race, re.I))
    return dict(gender_re=rx(e.get('gender')), race_re=rx(race).lstrip('^') if race else decline,
                hispanic=hisp, veteran_re=rx(e.get('veteran')), disability_re=rx(e.get('disability')))


def build():
    p = json.load(open(os.path.join(R, 'config/profile.json')))
    os.makedirs(os.path.join(EXT, 'resumes'), exist_ok=True)
    prof = dict(
        first_name=p['legal_first_name'], last_name=p['last_name'], full_name=p['name_legal'],
        email=p['email'], phone=re.sub(r'\D', '', p['phone']), linkedin=p['linkedin'],
        address=dict(street=p['mailing_address_street'], city=p['mailing_address_city'], zip=p['mailing_address_zip'],
                     state_name=STATES.get(p['mailing_address_state'], p['mailing_address_state'])),
        education=dict(school=p['school_full_name'], field=p['major']),
        answers=answers(p), eeo=eeo(p.get('eeo') or {}),
        experience=sorted(experience() + p.get('extra_experience', []),
                          key=lambda j: -(j['start_year'] * 12 + j['start_month'])),
        skills=skills())
    json.dump(prof, open(os.path.join(EXT, 'profile.json'), 'w'), indent=1)
    shutil.copy(os.path.join(R, p['resume']), os.path.join(EXT, 'resumes', 'default.pdf'))
    if not os.path.exists(os.path.join(EXT, 'resumes.json')):
        json.dump([], open(os.path.join(EXT, 'resumes.json'), 'w'))
    print(f"built profile.json ({len(prof['experience'])} jobs) and default resume in {EXT}")


def api_url(url):
    u = re.match(r'https?://([^/]+)/(?:[a-z]{2}-[A-Z]{2}/)?([^/]+)/(job/.+?)(?:/apply.*)?$', url.split('?')[0])
    if not u: sys.exit('not a Workday job URL: ' + url)
    host, site, path = u.groups()
    return f'https://{host}/wday/cxs/{host.split(".")[0]}/{site}/{path}', host, site, path


def add(url):
    api, host, site, path = api_url(url)
    d = json.load(urllib.request.urlopen(urllib.request.Request(api, headers={'User-Agent': 'Mozilla/5.0'}), timeout=20))['jobPostingInfo']
    desc = re.sub(r'\n\s*\n+', '\n', html.unescape(re.sub('<[^>]+>', '\n', d['jobDescription'])))
    tenant = host.split('.')[0]
    job_id = path.rsplit('_', 1)[-1]
    slug = f"{tenant}-{re.sub(r'[^a-z0-9]+', '-', d['title'].lower()).strip('-')[:50]}"
    fd = os.path.join(R, 'applications/files', slug)
    os.makedirs(fd, exist_ok=True)
    open(os.path.join(fd, 'posting.txt'), 'w').write(f"{d['title']}\n{d.get('location', '')}\n{url}\n\n{desc}")
    r = subprocess.run(['python3', os.path.join(R, 'scripts/tailor.py'), slug], capture_output=True, text=True)
    print('\n'.join(l for l in r.stdout.splitlines() if l.startswith(('check', 'render'))))
    pdf = os.path.join(fd, resume_filename())
    if r.returncode or not os.path.exists(pdf): sys.exit('tailoring failed:\n' + r.stderr[-800:])
    name = f"{tenant.upper() if len(tenant) <= 4 else tenant.capitalize()} - {re.sub(r'[/:]', '-', d['title'])}"
    os.makedirs(DESK, exist_ok=True)
    shutil.copy(pdf, os.path.join(DESK, f'{full_name()} Resume - {name}.pdf'))
    fname = f'resumes/{slug}.pdf'
    shutil.copy(pdf, os.path.join(EXT, fname))
    reg = json.load(open(os.path.join(EXT, 'resumes.json')))
    reg = [x for x in reg if x['jobId'] != job_id] + [dict(jobId=job_id, title=name, file='data/' + fname, url=url, skills=skills_for(desc))]
    json.dump(reg, open(os.path.join(EXT, 'resumes.json'), 'w'), indent=1)
    print(f'registered {name} (job id {job_id}); reload the extension')


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'list'
    if cmd == 'build': build()
    elif cmd == 'add':
        if not os.path.exists(os.path.join(EXT, 'profile.json')): build()
        for u in sys.argv[2:]: add(u)
    elif cmd == 'import':
        src = sys.argv[2] if len(sys.argv) > 2 else os.path.expanduser('~/Downloads/workday-learned.json')
        dst = os.path.join(EXT, 'learned.json')
        base = json.load(open(dst)) if os.path.exists(dst) else {}
        new = json.load(open(src))
        base.update(new)
        json.dump(base, open(dst, 'w'), indent=1)
        for k, v in new.items(): print(f"{v.get('question', k)[:90]} -> {v['value']}")
        print(f'{len(new)} imported, {len(base)} total in {dst}; reload the extension')
    else:
        for x in json.load(open(os.path.join(EXT, 'resumes.json'))): print(x['jobId'], '|', x['title'])
