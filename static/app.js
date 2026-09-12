const $ = id => document.getElementById(id);
let scJob = null;
let selectedProfile = null;
let token, connected = false, busy = false, chip = '', offset = 0, total = 0, searchTimer, searchVersion = 0, infoVersion = 0, shownResult = null;
async function api(path, options) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}
function error(e) { $('error').textContent = e.message; $('error').hidden = false; }
function controls() {
  const profileReady = !selectedProfile?.adapter_required || $('profileAdapterConfirm').checked;
  $('read').disabled = !token || !connected || busy || scJob?.status === 'waiting_bank' || !chip || !$('confirm').checked || !profileReady;
  $('profileAdapterLabel').hidden = !selectedProfile?.adapter_required;
  $('profileAdapterConfirm').disabled = busy;
  $('scRead').disabled = !token || !connected || busy || !$('adapterConfirm').checked;
  $('adapterConfirm').disabled = busy;
  const nextBank = scJob?.status === 'waiting_bank';
  $('scRead').textContent = busy && scJob?.profile === 'sc88' ? '読み出し中…' : nextBank ? 'Bank 1を読み出して結合' : 'Bank 0を読み出す';
  $('bankInstruction').textContent = nextBank ? '02 / Bank 1へ切替 · ROM A18 = 1' : '01 / Bank 0 · ROM A18 = 0';
  $('search').disabled = $('chips').disabled = $('confirm').disabled = busy;
  $('read').textContent = busy ? '読み出し中…' : '↓ ROMを読み出す';
}
function clearPinout() {
  selectedProfile = null;
  $('pinout').hidden = true;
  $('pins').replaceChildren();
  $('pinWarnings').replaceChildren();
  $('profileAdapterConfirm').checked = false;
  $('profileAdapterLabel').hidden = true;
}
function renderPinout(data) {
  clearPinout();
  if (!data.pins?.length) return;
  selectedProfile = {kind: data.profile, name: data.name, adapter_required: data.adapter_required === true, reader_chip: data.reader_chip};
  $('pinoutTitle').textContent = `${data.name || 'ROM'} · ピンアサイン`;
  $('pins').replaceChildren(...data.pins.map(pin => {
    const row = document.createElement('tr');
    row.className = `pin-${String(pin.role || '').toLowerCase()}`;
    for (const value of [String(pin.pin).padStart(2, '0'), pin.signal, pin.role, pin.note]) {
      const cell = document.createElement('td'); cell.textContent = value; row.append(cell);
    }
    return row;
  }));
  $('pinWarnings').replaceChildren(...(data.warnings || []).map(warning => {
    const item = document.createElement('p'); item.textContent = `! ${warning}`; return item;
  }));
  $('pinout').hidden = false;
}
async function status() {
  try {
    const state = await api('/api/status');
    connected = state.connected === true;
    $('status').textContent = state.busy ? '読み出し中' : connected ? 'TL866CS 接続済み' : 'TL866CS 未接続';
    $('status').className = 'badge' + (connected ? ' connected' : '');
    $('connection').textContent = state.log || 'USBケーブルを確認してください。';
  } catch (e) { connected = false; $('status').textContent = '接続確認エラー'; $('status').className = 'badge'; $('connection').textContent = e.message; }
  controls();
}
async function search() {
  const version = ++searchVersion;
  try {
    const data = await api('/api/devices?q=' + encodeURIComponent($('search').value));
    if (version !== searchVersion) return;
    $('chips').replaceChildren(...data.devices.map(name => { const o = document.createElement('option'); o.value = o.textContent = name; return o; }));
    $('chips').selectedIndex = -1;
    $('count').textContent = `${data.total.toLocaleString()} 件` + (data.total > 150 ? ' · 先頭150件を表示。型番を絞り込んでください。' : '');
  } catch (e) { if (version === searchVersion) error(e); }
}
$('search').addEventListener('input', () => {
  clearTimeout(searchTimer); ++searchVersion; ++infoVersion; chip = ''; $('confirm').checked = false; $('chips').replaceChildren(); $('info').textContent = '候補を選択してください。'; clearPinout(); controls(); searchTimer = setTimeout(search, 200);
});
$('chips').addEventListener('change', async () => {
  const version = ++infoVersion, selected = $('chips').value;
  chip = ''; $('confirm').checked = false; clearPinout(); controls();
  try { const d = await api('/api/device?name=' + encodeURIComponent(selected)); if (version !== infoVersion) return; $('info').textContent = d.info; renderPinout(d); chip = selected; controls(); } catch (e) { error(e); }
});
$('confirm').addEventListener('change', controls);
$('profileAdapterConfirm').addEventListener('change', controls);
$('refresh').addEventListener('click', status);
async function hex(at = 0) {
  const d = await api('/api/hex?offset=' + at); offset = d.offset; total = d.size;
  $('hex').textContent = d.text; $('range').textContent = `${offset.toString(16).toUpperCase().padStart(8, '0')} / ${total.toLocaleString()} bytes`;
  $('prev').disabled = offset === 0; $('next').disabled = offset + 256 >= total;
}
$('prev').onclick = () => hex(Math.max(0, offset - 256)).catch(error);
$('next').onclick = () => hex(offset + 256).catch(error);
$('jump').onclick = () => { const value = $('offset').value.trim(); if (!/^(0x)?[0-9a-f]+$/i.test(value)) return error(new Error('16進数のアドレスを入力してください。')); const address = parseInt(value, 16); if (!Number.isSafeInteger(address) || address >= total) return error(new Error('ROMの容量内のアドレスを入力してください。')); hex(address).catch(error); };
$('read').onclick = async () => {
  if ($('read').disabled) return;
  $('error').hidden = true; busy = true; controls();
  try {
    const request = { chip, confirmed: $('confirm').checked };
    if (selectedProfile) request.profile_adapter_confirmed = $('profileAdapterConfirm').checked;
    await api('/api/read', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Session-Token': token }, body: JSON.stringify(request) });
    shownResult = null; $('download').hidden = $('hexPanel').hidden = true; $('empty').hidden = false;
    $('resultChip').textContent = chip; $('size').textContent = $('duration').textContent = '—';
    await poll();
  } catch (e) { busy = false; controls(); error(e); }
};
async function poll() {
  try {
    const job = await api('/api/job');
    if (job.profile === 'sc88') {
      if (scJob?.status !== job.status) $('adapterConfirm').checked = false;
      scJob = job;
      $('scProgress').textContent = job.status === 'waiting_bank' ? 'Bank 0取得済み。処理は停止中です。バンクを切り替えて再確認してください。' : job.status === 'done' ? '2 / 2 banks · 1 MiB結合済み' : job.status === 'error' ? '失敗。Bank 0からやり直してください。' : `Bank ${job.bank} 読み出し中`;
    } else scJob = null;
    busy = job.status === 'running'; controls();
    $('jobStatus').textContent = ({ idle: '待機中', waiting_bank: 'Bank 1の切替待ち', running: '読み出し中…', done: '読み出し完了', error: '読み出し失敗' })[job.status];
    if (job.log) { $('log').textContent = job.log; $('log').scrollTop = $('log').scrollHeight; }
    if (job.status === 'error') error(new Error(job.error));
    if (job.status === 'done' && shownResult !== job.started) {
      await hex(0); shownResult = job.started;
      $('empty').hidden = true; $('hexPanel').hidden = $('download').hidden = false;
      $('resultChip').textContent = job.chip; $('size').textContent = job.size.toLocaleString() + ' B'; $('duration').textContent = job.seconds + ' s'; $('hash').textContent = job.sha256; $('uniform').hidden = !(job.uniform || job.banks_identical);
      $('uniform').textContent = job.banks_identical ? 'Bank 0とBank 1が完全一致しています。バンク切替が反映されているか確認してください。' : '全バイトが同じ値です。空のROMや接触不良の可能性があります。';
    }
    if (busy) setTimeout(poll, 500); else await status();
  } catch (e) { error(e); if (busy) setTimeout(poll, 1500); }
}
(async () => { try { token = (await api('/api/session')).token; await Promise.all([status(), search()]); await poll(); } catch (e) { error(e); } })();

$('adapterConfirm').addEventListener('change', controls);
$('scRead').onclick = async () => {
  if ($('scRead').disabled) return;
  const bank = scJob?.status === 'waiting_bank' ? 1 : 0;
  busy = true; controls(); $('error').hidden = true;
  try {
    await api('/api/sc88/read', {method:'POST', headers:{'Content-Type':'application/json','X-Session-Token':token}, body:JSON.stringify({bank, job_id:scJob?.id, adapter_confirmed:$('adapterConfirm').checked, confirmed:true})});
    shownResult = null; $('adapterConfirm').checked = false;
    $('download').hidden = $('hexPanel').hidden = true; $('empty').hidden = false;
    $('resultChip').textContent = 'SC-88Pro PRG ROM'; $('size').textContent = $('duration').textContent = '—';
    await poll();
  } catch(e) {busy = false; controls(); error(e);}
};
