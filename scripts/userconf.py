"""Per-user values the helper scripts share, read from config/ so nothing personal is hardcoded.

    resume_filename()  -> "First-Last-Resume.pdf" from profile.json (the name recruiters see on the upload)
    tracker_path()     -> outreach tracker JSON (settings.json outreach.tracker_path, default state/outreach-tracker.json)
    export_dir()       -> folder for copies of tailored resumes (settings.json documents.export_dir, default ~/Desktop/custom resume)
"""
import json, os, re

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name):
    try:
        return json.load(open(os.path.join(R, 'config', name)))
    except (OSError, ValueError):
        return {}


def full_name():
    p = _load('profile.json')
    return ' '.join(x for x in (p.get('first_name'), p.get('last_name')) if x).strip() or 'Resume'


def resume_filename():
    n = re.sub(r'[^A-Za-z0-9]+', '-', full_name()).strip('-')
    return f'{n}-Resume.pdf' if n != 'Resume' else 'Resume.pdf'


def tracker_path():
    p = (_load('settings.json').get('outreach') or {}).get('tracker_path') or 'state/outreach-tracker.json'
    p = os.path.expanduser(p)
    return p if os.path.isabs(p) else os.path.join(R, p)


def export_dir():
    return os.path.expanduser((_load('settings.json').get('documents') or {}).get('export_dir') or '~/Desktop/custom resume')
