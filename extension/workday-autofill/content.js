// please-hire-me Workday Autofill — content script.
// Fills each Workday step as it appears while a session is active. It never clicks Submit,
// never ticks terms/acknowledgment boxes, and leaves anything it cannot answer highlighted.

(() => {
  const KEY = 'phm_autofill_active';
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  let P = null;          // profile.json
  let RES = [];          // resumes.json
  let running = false;
  let lastStep = '';
  const log = [];        // what this step filled / needs

  // ---------- data ----------
  async function load() {
    if (P) return;
    P = await (await fetch(chrome.runtime.getURL('data/profile.json'))).json();
    try { RES = await (await fetch(chrome.runtime.getURL('data/resumes.json'))).json(); } catch (e) { RES = []; }
  }
  function matchResume() {
    const href = decodeURIComponent(location.href);
    return RES.find((r) => r.jobId && href.includes(r.jobId)) || null;
  }

  // ---------- DOM helpers ----------
  const q = (sel, root = document) => root.querySelector(sel);
  const qa = (sel, root = document) => [...root.querySelectorAll(sel)];
  const visible = (el) => !!el && el.offsetParent !== null;
  const aid = (id, root = document) => q(`[data-automation-id="${id}"]`, root);
  // Workday has two layouts: older data-automation-id names (legalNameSection_firstName) and newer
  // "<section>--<field>" ids with name="<field>" (name--legalName--firstName). f() tries every given name
  // as an automation id, an id suffix and a name attribute.
  const f = (...names) => {
    for (const n of names) {
      const el = q(`[data-automation-id="${n}"]`) || q(`[id$="--${n}"]`) || q(`[name="${n}"]`);
      if (el && visible(el)) return el;
    }
    return null;
  };

  function setNative(el, value) {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, value);
  }
  // Type the way a person's keyboard does (insertText fires beforeinput/input that Workday's form state
  // records); fall back to the native setter only if that did not take.
  async function setText(el, value, label) {
    if (!el || value == null || value === '') return false;
    value = String(value);
    if (el.value === value && el.getAttribute('aria-invalid') !== 'true') return true;
    el.focus();
    el.dispatchEvent(new FocusEvent('focusin', { bubbles: true }));
    if (el.select) el.select();
    document.execCommand('selectAll', false);
    document.execCommand('insertText', false, value);
    if (el.value !== value) {
      setNative(el, value);
      el.dispatchEvent(new Event('input', { bubbles: true }));
    }
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.dispatchEvent(new FocusEvent('focusout', { bubbles: true }));
    el.blur();
    await sleep(120);
    if (label) log.push(['filled', label]);
    return true;
  }
  function fullClick(el) {
    for (const t of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
      el.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window }));
    }
  }
  async function waitFor(fn, ms = 2500) {
    const t0 = Date.now();
    while (Date.now() - t0 < ms) { const v = fn(); if (v) return v; await sleep(100); }
    return null;
  }
  // Workday single-select: a button[aria-haspopup=listbox]; options render in a portal.
  async function pickOption(button, re, label) {
    if (!button) return false;
    const cur = (button.textContent || '').trim();
    if (re.test(cur) && !/select one/i.test(cur)) return true;
    fullClick(button);
    const opt = await waitFor(() => qa('[role="option"]').filter(visible).find((o) => re.test(o.textContent.trim())));
    if (!opt) {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      if (label) log.push(['needs you', label]);
      return false;
    }
    fullClick(opt);
    await sleep(250);
    if (label) log.push(['filled', label]);
    return true;
  }
  // Searchable prompt (school, field of study, "how did you hear"): type, Enter, click first matching row.
  async function promptSearch(input, text, re, label) {
    if (!input) return false;
    input.focus();
    setNative(input, text);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true }));
    const opt = await waitFor(() => qa('[data-automation-id="promptOption"], [role="option"]').filter(visible)
      .find((o) => re.test(o.textContent.trim())), 3000);
    if (!opt) { if (label) log.push(['needs you', label]); return false; }
    fullClick(opt);
    await sleep(300);
    if (label) log.push(['filled', label]);
    return true;
  }
  function labelOf(container) {
    const l = q('label, legend', container);
    return ((l && l.textContent) || container.textContent || '').replace(/\s+/g, ' ').trim();
  }
  function fieldContainers() {
    return qa('[data-automation-id^="formField-"], fieldset').filter(visible);
  }
  function findField(re) {
    return fieldContainers().find((c) => re.test(labelOf(c)));
  }
  async function chooseRadio(container, re, label) {
    const radios = qa('input[type="radio"]', container);
    const r = radios.find((x) => {
      const l = x.id && q(`label[for="${x.id}"]`);
      return re.test(((l && l.textContent) || x.value || x.getAttribute('aria-label') || '').trim());
    });
    if (!r) return false;
    if (!r.checked) { fullClick(r); await sleep(150); }
    log.push(['filled', label]);
    return true;
  }
  // Answer a Yes/No style question whatever widget Workday used.
  async function answer(container, re, label) {
    const btn = q('button[aria-haspopup="listbox"]', container);
    if (btn) return pickOption(btn, re, label);
    if (q('input[type="radio"]', container)) return chooseRadio(container, re, label);
    return false;
  }
  const clean = (s) => String(s).replace(/[<>\[\]{}\\]/g, '').replace(/"/g, '”');

  // ---------- learned answers ----------
  // When a field the extension could not answer is answered by hand, clicking Save and Continue stores
  // question -> answer in chrome.storage.local. Next time the same question appears (any company), it is
  // filled from there. Export from the popup to fold them into config/answers.md.
  const reEsc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const labelKey = (t) => t.toLowerCase().replace(/\*/g, '').replace(/\b(nike|hpe|deckers|converse|teva)\b/g, 'company')
    .replace(/[^a-z0-9 ]+/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 160);
  // Base answers saved into the repo (data/learned.json via workday_ext.py import) + this browser's own.
  let seed = null;
  async function learned() {
    if (seed === null) {
      try { seed = await (await fetch(chrome.runtime.getURL('data/learned.json'))).json(); } catch (e) { seed = {}; }
    }
    return { ...seed, ...((await chrome.storage.local.get('learned')).learned || {}) };
  }
  function hasValue(c) {
    const btn = q('button[aria-haspopup="listbox"]', c);
    if (btn) return !/^select one$/i.test(btn.textContent.trim()) && !!btn.textContent.trim();
    if (qa('input[type="radio"]', c).some((x) => x.checked)) return true;
    if (q('[data-automation-id="selectedItem"], [data-automation-id="selectedItemList"] li', c)) return true;
    const t = q('input[type="text"], input:not([type]), textarea', c);
    return !!(t && t.value);
  }
  function readAnswer(c) {
    const btn = q('button[aria-haspopup="listbox"]', c);
    if (btn) { const t = btn.textContent.trim(); return t && !/^select one$/i.test(t) ? { kind: 'select', value: t } : null; }
    const r = qa('input[type="radio"]', c).find((x) => x.checked);
    if (r) { const l = r.id && q(`label[for="${r.id}"]`); return { kind: 'radio', value: ((l && l.textContent) || r.value).trim() }; }
    const t = q('input[type="text"], textarea', c);
    if (t && t.value && t.value.length <= 300 && !/date|signature|name/i.test(labelOf(c))) return { kind: 'text', value: t.value };
    return null;
  }
  async function applyLearned(c, a, label) {
    if (a.kind === 'select') return pickOption(q('button[aria-haspopup="listbox"]', c), new RegExp('^' + reEsc(a.value) + '$', 'i'), label + ' (learned)');
    if (a.kind === 'radio') return chooseRadio(c, new RegExp('^' + reEsc(a.value) + '$', 'i'), label + ' (learned)');
    if (a.kind === 'text') { const t = q('input[type="text"], textarea', c); if (t && !t.value) return setText(t, a.value, label + ' (learned)'); }
    return false;
  }
  let pending = [];   // containers flagged on the current step
  document.addEventListener('click', async (e) => {
    const b = e.target.closest && e.target.closest('button');
    if (!b || !/save and continue|next|continue/i.test(b.textContent || '') || !pending.length) return;
    const L = await learned();
    let n = 0;
    for (const c of pending) {
      if (!document.contains(c)) continue;
      const a = readAnswer(c);
      if (a) { L[labelKey(labelOf(c))] = { ...a, question: labelOf(c).slice(0, 200), from: location.host, date: new Date().toISOString().slice(0, 10) }; n++; }
    }
    pending = [];
    if (n) await chrome.storage.local.set({ learned: L });
  }, true);

  // ---------- steps ----------
  async function stepMyInformation() {
    const a = P.address;
    // "Have you previously worked for <company>?" is a radio set near the top.
    const prev = q('[data-automation-id="formField-candidateIsPreviousWorker"]') ||
      findField(/previously (worked|been employed)|ever been employed|employed by|former (employee|worker)|worked for .* before/i);
    if (prev) await answer(prev, /^no\b/i, 'Previously worked here: No');
    const country = f('countryDropdown', 'country') || q('[data-automation-id="formField-countryDropdown"] button');
    if (country) await pickOption(country, /^United States of America$/i, 'Country');
    await setText(f('legalNameSection_firstName', 'legalName--firstName', 'firstName'), P.first_name, 'First name');
    await setText(f('legalNameSection_lastName', 'legalName--lastName', 'lastName'), P.last_name, 'Last name');
    await setText(f('addressSection_addressLine1', 'addressLine1'), a.street, 'Address');
    await setText(f('addressSection_city', 'city'), a.city, 'City');
    await setText(f('addressSection_postalCode', 'postalCode'), a.zip, 'ZIP');
    const state = f('addressSection_countryRegion', 'countryRegion') ||
      q('[data-automation-id="formField-countryRegion"] button');
    if (state) await pickOption(state, new RegExp('^' + a.state_name + '$', 'i'), 'State');
    const dev = f('phone-device-type', 'phoneType') || q('[data-automation-id="formField-phoneType"] button');
    if (dev) await pickOption(dev, /^mobile$/i, 'Phone type');
    await setText(f('phone-number', 'phoneNumber'), P.phone, 'Phone');
    const email = f('email');
    if (email && !email.readOnly && !email.disabled) await setText(email, P.email, 'Email');
    // How did you hear: a multiselect that is often two levels (category > source). Truthful default:
    // the company's own careers site, where the application is being made.
    const src = q('[data-automation-id="formField-source"]') || findField(/how did you hear/i);
    if (src && !hasValue(src)) {
      const prefs = [/company (web)?site|career(s)? (site|page|website)|corporate (web)?site|^website$|company career/i,
                     /website|career|online|internet|job board/i];
      const inp = q('input', src);
      if (inp) { fullClick(inp); inp.focus(); }
      for (let level = 0; level < 3 && !hasValue(src); level++) {
        const opts = await waitFor(() => { const o = qa('[data-automation-id="promptOption"], [role="option"]').filter(visible); return o.length ? o : null; }, 2500);
        if (!opts) break;
        const pick = prefs.map((re) => opts.find((o) => re.test(o.textContent.trim()))).find(Boolean);
        if (!pick) break;
        fullClick(pick);
        await sleep(700);
      }
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      log.push([hasValue(src) ? 'filled' : 'needs you', 'How did you hear' + (hasValue(src) ? ': company careers site' : '')]);
    }
    const pref = findField(/preferred name/i);
    if (pref) log.push(['skipped', 'Preferred name (same as legal)']);
  }

  async function uploadResume() {
    const input = aid('file-upload-input-ref') || q('input[type="file"]');
    if (!input) return;
    if (qa('[data-automation-id="file-upload-item"], [data-automation-id="file-upload-successful"]').length) {
      log.push(['skipped', 'Resume already attached']);
      return;
    }
    const m = matchResume();
    const path = m ? m.file : 'data/resumes/default.pdf';
    const blob = await (await fetch(chrome.runtime.getURL(path))).blob();
    const file = new File([blob], `${P.first_name}-${P.last_name}-Resume.pdf`.replace(/\s+/g, '-'), { type: 'application/pdf' });
    const dt = new DataTransfer();
    dt.items.add(file);
    input.files = dt.files;
    input.dispatchEvent(new Event('change', { bubbles: true }));
    log.push(['filled', 'Resume: ' + (m ? m.title : 'base resume')]);
    await sleep(2500);
  }

  // Dates: newer layout ids "<panel>--startDate-dateSectionMonth-input"; older wraps them in formField-startDate.
  async function fillDate(scope, which, m, y) {
    const box = q(`[data-automation-id="formField-${which}"]`, scope) || scope;
    const mm = q(`input[id$="--${which}-dateSectionMonth-input"]`, scope) || q('[data-automation-id="dateSectionMonth-input"]', box);
    const yy = q(`input[id$="--${which}-dateSectionYear-input"]`, scope) || q('[data-automation-id="dateSectionYear-input"]', box);
    if (mm) await setText(mm, String(m).padStart(2, '0'));
    if (yy) await setText(yy, String(y));
  }
  // Section = [role=group] labelled e.g. "Work-Experience-section" (new) or automation id (old).
  function section(re) {
    return qa('[role="group"], [data-automation-id$="Section"]').filter(visible)
      .find((g) => re.test((g.getAttribute('aria-labelledby') || g.getAttribute('data-automation-id') || '').replace(/[-_]/g, ' ')));
  }
  async function addPanel(sectionRe, inputSuffix, have) {
    const sec = section(sectionRe);
    const add = sec && (q('[data-automation-id="add-button"]', sec) ||
      qa('button', sec).find((x) => /^add( another)?$/i.test(x.textContent.trim())));
    if (!add) return null;
    fullClick(add);
    return waitFor(() => { const t = qa(`input[id$="${inputSuffix}"]`).filter(visible); return t.length > have ? t : null; });
  }

  async function stepMyExperience() {
    await uploadResume();
    await sleep(800);  // upload re-renders the page
    const jobs = P.experience;
    for (let i = 0; i < jobs.length; i++) {
      let titles = qa('input[id$="--jobTitle"]').filter(visible);
      if (titles.length <= i) titles = await addPanel(/work experience/i, '--jobTitle', i);
      if (!titles) { log.push(['needs you', `Could not add work entry ${i + 1}`]); break; }
      const t = titles[i];
      const pre = t.id.replace(/--jobTitle$/, '');
      const scope = t.closest('[role="group"][aria-labelledby$="-panel"]') || t.closest('[role="group"]') || document;
      const j = jobs[i];
      await setText(t, j.title);
      await setText(q(`[id="${pre}--companyName"]`) || q('input[id$="--companyName"]', scope), j.company);
      await setText(q(`[id="${pre}--location"]`) || q('input[id$="--location"]', scope), j.location);
      const cur = q(`[id="${pre}--currentlyWorkHere"]`) || q('input[id$="--currentlyWorkHere"]', scope);
      if (cur && cur.checked !== !!j.current) { fullClick(cur); await sleep(300); }
      await fillDate(scope, 'startDate', j.start_month, j.start_year);
      if (!j.current) await fillDate(scope, 'endDate', j.end_month, j.end_year);
      await setText(q(`[id="${pre}--roleDescription"]`) || q('textarea[id$="--roleDescription"]', scope), clean(j.description));
      log.push(['filled', `Work: ${j.title}, ${j.company}`]);
    }
    // Second pass: the resume parser can re-render entry 1 after upload, and Workday sometimes drops a value.
    await sleep(1500);
    const titles2 = qa('input[id$="--jobTitle"]').filter(visible);
    let fixed = 0;
    for (let i = 0; i < Math.min(jobs.length, titles2.length); i++) {
      const j = jobs[i], pre = titles2[i].id.replace(/--jobTitle$/, '');
      const want = [[pre + '--jobTitle', j.title], [pre + '--companyName', j.company], [pre + '--location', j.location],
        [pre + '--startDate-dateSectionMonth-input', String(j.start_month).padStart(2, '0')],
        [pre + '--startDate-dateSectionYear-input', String(j.start_year)]];
      if (!j.current) want.push([pre + '--endDate-dateSectionMonth-input', String(j.end_month).padStart(2, '0')],
        [pre + '--endDate-dateSectionYear-input', String(j.end_year)]);
      for (const [id, v] of want) {
        const el = document.getElementById(id);
        if (el && v && (el.value.replace(/^0/, '') !== v.replace(/^0/, '') || el.getAttribute('aria-invalid') === 'true')) { await setText(el, v); fixed++; }
      }
      const cur = document.getElementById(pre + '--currentlyWorkHere');
      if (cur && cur.checked !== !!j.current) { fullClick(cur); fixed++; await sleep(300); }
    }
    if (fixed) log.push(['filled', `Re-checked work history: fixed ${fixed} field${fixed > 1 ? 's' : ''}`]);
    const errs = qa('[aria-invalid="true"]').filter(visible).length;
    if (errs) log.push(['needs you', `${errs} field${errs > 1 ? 's' : ''} still marked invalid: retype them (click, select all, type)`]);
    const extra = qa('input[id$="--jobTitle"]').filter(visible).length - jobs.length;
    if (extra > 0) log.push(['needs you', `${extra} extra work entr${extra > 1 ? 'ies' : 'y'}: delete them`]);

    // Education (one entry).
    let school = q('input[id$="--school"], input[id$="--schoolName"]') ||
      q('[data-automation-id="formField-school"] input, [data-automation-id="formField-schoolName"] input');
    if (!school && section(/education/i)) {
      const got = await addPanel(/education/i, '--schoolName', 0) || await addPanel(/education/i, '--school', 0);
      school = got && got[0];
    }
    if (school) {
      const box = school.closest('[data-automation-id^="formField-"]') || school.parentElement;
      if (q('[data-automation-id="multiSelectContainer"], [data-automation-id="promptIcon"]', box)) {
        await promptSearch(school, P.education.school, /pitzer/i, 'School');
      } else await setText(school, P.education.school, 'School');
      const deg = q('button[id$="--degree"]') || q('[data-automation-id="formField-degree"] button');
      if (deg) await pickOption(deg, /bachelor/i, 'Degree');
      const fos = q('input[id$="--fieldOfStudy"]') ||
        q('[data-automation-id="formField-field-of-study"] input, [data-automation-id="formField-fieldOfStudy"] input');
      if (fos) await promptSearch(fos, P.education.field, /economics/i, 'Field of study');
    }
    // LinkedIn / websites.
    const li = aid('linkedinQuestion') || q('input[id$="--linkedInAccount"], input[id$="--linkedin"]');
    if (li) await setText(li, P.linkedin, 'LinkedIn');
    let url = q('input[id$="--url"]');
    if (!url && section(/website/i)) { const got = await addPanel(/website/i, '--url', 0); url = got && got[0]; }
    if (url && !url.value) await setText(url, P.linkedin, 'Website: LinkedIn');

    // Skills (only when the form has a skills box): resume tools, the posting's matches first.
    const sk = q('input[id$="--skills"]') || q('[data-automation-id="formField-skills"] input') ||
      (section(/skills/i) && q('input', section(/skills/i)));
    if (sk) {
      const m = matchResume();
      const list = (m && m.skills && m.skills.length ? m.skills : P.skills || []).slice(0, 10);
      let n = 0;
      for (const s of list) {
        if (await promptSearch(sk, s, new RegExp('^' + reEsc(s) + '$', 'i'), null)) n++;
        document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
        await sleep(200);
      }
      log.push([n ? 'filled' : 'needs you', `Skills: ${n} of ${list.length} added (${list.join(', ')})`]);
    }
  }

  // Application Questions: rule table on the question text. Anything unmatched is left for you.
  // Facts come from your profile (P.answers, built by scripts/workday_ext.py); an unknown fact is left for you.
  const yn = (v) => (v === true ? /^yes/i : v === false ? /^no/i : null);
  const A = () => P.answers || {};
  const RULES = () => [
    [/unrestricted work authori/i, yn(A().unrestricted), 'Unrestricted work authorization'],
    [/legally (authori[sz]ed|eligible)|authori[sz]ed to work|eligible to work/i, yn(A().authorized), 'Authorized to work'],
    [/sponsor/i, yn(A().sponsorship), 'Sponsorship'],
    [/us citizen|u\.s\. citizen|citizenship/i, yn(A().citizen), 'US citizen'],
    [/relocat/i, yn(A().relocate), 'Willing to relocate'],
    // Defaults that hold for most applicants (same conventions as config/answers.md). Edit if they don't hold for you.
    [/18 years|age of 18|at least 18/i, /^yes/i, '18 or older'],
    [/previously (worked|been employed)|ever been employed|former employee|currently (employed|work) (by|for|at)/i, /^no/i, 'Previously/currently employed here'],
    [/relative|family member|related to/i, /^no/i, 'Relatives at company'],
    [/non-?compete|non-?solicit/i, /^no/i, 'Non-compete'],
    [/government|public official|military/i, /^no/i, 'Government/military employment'],
    [/background check|drug (test|screen)/i, /^yes/i, 'Background check consent'],
    [/text message|sms/i, /^no/i, 'Text messages'],
  ].filter((r) => r[1]);
  async function stepQuestions() {
    const L = await learned();
    for (const c of fieldContainers()) {
      const text = labelOf(c);
      const known = L[labelKey(text)];
      const rule = RULES().find(([re]) => re.test(text));
      const hasWidget = q('button[aria-haspopup="listbox"], input[type="radio"]', c);
      const ta = !hasWidget && q('textarea, input[type="text"]', c);
      if (!hasWidget && !ta) continue;
      if (hasValue(c)) continue;                                     // already answered
      if (rule && hasWidget && (await answer(c, rule[1], rule[2]))) continue;
      if (known && (await applyLearned(c, known, text.slice(0, 60)))) continue;
      if (/\*|required/i.test(text) || c.querySelector('[aria-required="true"]')) {
        log.push(['needs you', text.slice(0, 80) + ' (your answer will be remembered)']);
        pending.push(c);
      }
    }
  }
  // Any step: required fields still empty after filling get flagged and learned on Save and Continue.
  async function flagLeftovers() {
    const L = await learned();
    for (const c of fieldContainers()) {
      const text = labelOf(c);
      if (!/\*/.test(text) || hasValue(c) || pending.includes(c)) continue;
      const known = L[labelKey(text)];
      if (known && (await applyLearned(c, known, text.slice(0, 60)))) continue;
      if (q('button[aria-haspopup="listbox"], input[type="radio"], input[type="text"], textarea', c)) {
        log.push(['needs you', text.slice(0, 80) + ' (your answer will be remembered)']);
        pending.push(c);
      }
    }
  }

  async function stepVoluntary() {
    const e = P.eeo;
    const g = findField(/gender/i);
    if (g) await answer(g, new RegExp(e.gender_re, 'i'), 'Gender');
    const h = findField(/hispanic|latino/i);
    if (h && e.hispanic != null) await answer(h, e.hispanic ? /^yes/i : /^no/i, 'Hispanic/Latino');
    const eth = findField(/ethnicity|race/i);
    if (eth) await answer(eth, new RegExp(e.race_re, 'i'), 'Race/ethnicity');
    const v = findField(/veteran/i);
    if (v) await answer(v, new RegExp(e.veteran_re, 'i'), 'Veteran status');
    const agree = qa('input[type="checkbox"]').filter(visible).find((x) => !x.checked);
    if (agree) log.push(['needs you', 'Acknowledgment / terms checkbox: tick it yourself']);
  }

  async function stepSelfIdentify() {
    await setText(aid('name'), P.full_name, 'Self-ID name');
    const d = new Date();
    await fillDate(document, 'dateSignedOn', d.getMonth() + 1, d.getFullYear());
    const day = q('[data-automation-id="formField-dateSignedOn"] [data-automation-id="dateSectionDay-input"]');
    if (day) await setText(day, String(d.getDate()).padStart(2, '0'));
    const re = new RegExp(P.eeo.disability_re, 'i');
    const lab = qa('label').find((l) => re.test(l.textContent.trim()));
    const box = lab && (q('#' + CSS.escape(lab.htmlFor)) || q('input', lab.parentElement));
    if (box && !box.checked) { fullClick(box); log.push(['filled', 'Disability: ' + lab.textContent.trim()]); }
  }

  function currentStep() {
    if (f('legalNameSection_firstName', 'legalName--firstName')) return 'info';
    if (aid('file-upload-input-ref') || aid('select-files') || q('input[id$="--jobTitle"]') || section(/work experience/i)) return 'experience';
    if (q('[data-automation-id="formField-dateSignedOn"]') && /disability/i.test(document.body.innerText)) return 'selfid';
    if (findField(/veteran|gender|ethnicity/i)) return 'voluntary';
    if (/review/i.test((aid('progressBarActiveStep') || {}).textContent || '') || aid('reviewJobApplicationPage')) return 'review';
    if (fieldContainers().length) return 'questions';
    return '';
  }

  // ---------- panel ----------
  function panel(step) {
    let el = document.getElementById('phm-panel');
    if (!el) {
      el = document.createElement('div');
      el.id = 'phm-panel';
      el.style.cssText = 'position:fixed;right:16px;bottom:16px;z-index:2147483647;width:300px;max-height:45vh;overflow:auto;background:#111827;color:#f9fafb;font:12px -apple-system,system-ui,sans-serif;border-radius:10px;padding:10px 12px;box-shadow:0 8px 24px rgba(0,0,0,.3)';
      document.body.appendChild(el);
    }
    const needs = log.filter((x) => x[0] === 'needs you');
    const done = log.filter((x) => x[0] === 'filled');
    el.innerHTML = '';
    const h = document.createElement('div');
    h.style.cssText = 'font-weight:600;margin-bottom:6px';
    h.textContent = step === 'review'
      ? 'Review page: check everything, then Submit yourself.'
      : `Autofill: ${done.length} filled${needs.length ? `, ${needs.length} need you` : ''}. Review, then Save and Continue.`;
    el.appendChild(h);
    for (const [k, t] of needs.concat(done)) {
      const row = document.createElement('div');
      row.style.color = k === 'needs you' ? '#fbbf24' : '#9ca3af';
      row.textContent = (k === 'needs you' ? '⚠ ' : '✓ ') + t;
      el.appendChild(row);
    }
  }

  // ---------- run loop ----------
  async function run(force) {
    if (running) return;
    const step = currentStep();
    if (!step || (!force && step === lastStep)) return;
    running = true;
    lastStep = step;
    log.length = 0;
    pending = [];
    try {
      await load();
      await sleep(600); // let the step finish rendering
      if (step === 'info') await stepMyInformation();
      else if (step === 'experience') await stepMyExperience();
      else if (step === 'questions') await stepQuestions();
      else if (step === 'voluntary') await stepVoluntary();
      else if (step === 'selfid') await stepSelfIdentify();
      if (step !== 'review') await flagLeftovers();
      panel(step);
    } catch (e) {
      log.push(['needs you', 'Autofill error: ' + e.message]);
      panel(step);
    } finally {
      running = false;
    }
  }

  let timer = null;
  new MutationObserver(() => {
    if (sessionStorage.getItem(KEY) !== '1') return;
    clearTimeout(timer);
    timer = setTimeout(() => run(false), 800);
  }).observe(document.documentElement, { childList: true, subtree: true });

  chrome.runtime.onMessage.addListener((msg, _s, reply) => {
    (async () => {
      await load().catch(() => {});
      if (msg.type === 'status') reply({ active: sessionStorage.getItem(KEY) === '1', resume: matchResume(), step: currentStep() });
      else if (msg.type === 'start') { sessionStorage.setItem(KEY, '1'); lastStep = ''; reply({ ok: true }); run(true); }
      else if (msg.type === 'learned') reply(await learned());
      else if (msg.type === 'stop') { sessionStorage.removeItem(KEY); const p = document.getElementById('phm-panel'); if (p) p.remove(); reply({ ok: true }); }
    })();
    return true;
  });
})();
