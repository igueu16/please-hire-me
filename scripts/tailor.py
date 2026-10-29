#!/usr/bin/env python3
"""Per-posting resume tailoring for the bulk run: reorder each role's bullets by overlap with the
posting's vocabulary, then run check-resume and render-resume. Adds no words; the base resume and
fact sheet stay the only sources. Rewording toward posting spelling is done by hand after this.

    python3 scripts/tailor.py <slug>     # uses applications/files/<slug>/posting.txt
"""
import os, re, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from userconf import resume_filename
R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STOP = set("""a an the and or of to in for on with at by from as is are be this that you your our we will
our their they it its into about across more most who what when where which how than then also can
have has had not but all any each per other such these those role team work working company years year
experience ability strong including help build building new make makes product products""".split())
def words(t): return [w for w in re.findall(r"[a-z][a-z0-9+/#.-]{2,}", t.lower()) if w not in STOP]
slug = sys.argv[1]; d = os.path.join(R, 'applications/files', slug)
post = open(os.path.join(d, 'posting.txt')).read()
# score only the role description: drop benefits, pay and EEO boilerplate that follows it
post = re.split(r"(?im)^.*\b(benefits|perks|compensation|salary|pay range|base pay|equal (employment )?opportunity|what we offer)\b.*$", post)[0] or post
pw = words(post); freq = {}
for w in pw: freq[w] = freq.get(w, 0) + 1
big = {}
for a, b in zip(pw, pw[1:]): big[a + ' ' + b] = big.get(a + ' ' + b, 0) + 1
def score(bullet):
    bw = words(re.sub(r'\\[a-zA-Z]+', ' ', bullet)); s = sum(min(freq.get(w, 0), 3) for w in set(bw))
    s += sum(3 * min(big.get(a + ' ' + b, 0), 2) for a, b in zip(bw, bw[1:])); return s
tex = open(os.path.join(R, 'data/resume.tex')).read()
def reorder(block):
    items = re.findall(r"        \\resumeItem\{.*?\}\n", block, re.S)
    if len(items) < 2: return block
    ranked = sorted(items, key=lambda b: -score(b))
    out = block
    for i, b in enumerate(items): out = out.replace(b, f"@@{i}@@", 1)
    for i, b in enumerate(ranked): out = out.replace(f"@@{i}@@", b, 1)
    return out
parts = re.split(r"(\\resumeItemListStart\n.*?\\resumeItemListEnd)", tex, flags=re.S)
tex2 = ''.join(reorder(p) if p.startswith('\\resumeItemListStart') else p for p in parts)
open(os.path.join(d, 'resume.tex'), 'w').write(tex2)
top = sorted(((v, k) for k, v in big.items() if v > 1), reverse=True)[:12]
print('posting top phrases:', ', '.join(k for v, k in top))
for cmd in (['check-resume', os.path.join(d, 'resume.tex'), '--jd', os.path.join(d, 'posting.txt')],
            ['render-resume', os.path.join(d, 'resume.tex'), os.path.join(d, resume_filename())]):
    r = subprocess.run(['python3', os.path.join(R, 'scripts/app_docs.py')] + cmd, capture_output=True, text=True)
    print(cmd[0], '->', (r.stdout + r.stderr).strip().splitlines()[-1])
first = re.findall(r"\\resumeItemListStart\n        \\resumeItem\{(.{0,90})", tex2)
print('first bullet per role:'); [print('  -', f) for f in first]
