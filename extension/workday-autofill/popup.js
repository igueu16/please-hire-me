const $ = (id) => document.getElementById(id);

async function tab() {
  const [t] = await chrome.tabs.query({ active: true, currentWindow: true });
  return t;
}

async function send(msg) {
  const t = await tab();
  try {
    return await chrome.tabs.sendMessage(t.id, msg);
  } catch (e) {
    return null;
  }
}

(async () => {
  const r = await send({ type: 'status' });
  if (!r) {
    $('resume').innerHTML = '<span class="bad">Open a Workday application page first (myworkdayjobs.com).</span>';
    $('go').disabled = true;
    return;
  }
  const m = r.resume;
  $('resume').innerHTML = m
    ? `<b>Resume: ${m.title}</b>${m.file}`
    : '<b class="bad">No tailored resume for this posting</b>The base resume will be uploaded. Run scripts/workday_ext.py add &lt;url&gt; to tailor one.';
  if (r.active) $('go').textContent = 'Running… (click to re-fill this step)';
})();

$('go').onclick = async () => {
  await send({ type: 'start' });
  window.close();
};

chrome.storage.local.get('learned').then(({ learned }) => {
  $('nlearn').textContent = Object.keys(learned || {}).length;
});

$('export').onclick = async () => {
  const { learned } = await chrome.storage.local.get('learned');
  const blob = new Blob([JSON.stringify(learned || {}, null, 1)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'workday-learned.json';
  a.click();
};

$('stop').onclick = async () => {
  await send({ type: 'stop' });
  window.close();
};
