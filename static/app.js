const $ = id => document.getElementById(id);
let scJob = null;
let selectedProfile = null;
let token, connected = false, busy = false, chip = '', offset = 0, total = 0, searchTimer, searchVersion = 0, infoVersion = 0, shownResult = null, previewStamp = '', viewerHasData = false;
async function api(path, options) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}
function error(e) { $('error').textContent = e.message; $('error').hidden = false; }
function controls() {
  const profileReady = selectedProfile?.direct_supported ? $('profileDirectConfirm').checked : !selectedProfile?.adapter_required || $('profileAdapterConfirm').checked;
  $('read').disabled = !token || !connected || busy || scJob?.status === 'waiting_bank' || !chip || !$('confirm').checked || !profileReady;
  $('profileAdapterLabel').hidden = !selectedProfile?.adapter_required;
  $('profileDirectLabel').hidden = !selectedProfile?.direct_supported;
  $('profileAdapterConfirm').disabled = busy;
  $('profileDirectConfirm').disabled = busy;
  $('scRead').disabled = !token || !connected || busy || !$('adapterConfirm').checked;
  $('adapterConfirm').disabled = busy;
  $('readSpeed').disabled = busy;
  $('cancel').hidden = !busy;
  $('cancel').disabled = scJob?.status === 'canceling' || $('jobStatus').dataset.state === 'canceling';
  $('cancel').textContent = $('cancel').disabled ? 'キャンセル処理中…' : '■ 読み出しをキャンセル';
  $('clear').disabled = busy || !viewerHasData;
  const nextBank = scJob?.status === 'waiting_bank';
  $('scRead').textContent = busy && scJob?.profile === 'sc88' ? '読み出し中…' : nextBank ? 'Bank 1を読み出して結合' : 'Bank 0を読み出す';
  $('bankInstruction').textContent = nextBank ? '02 / Bank 1へ切替 · ROM A18 = 1' : '01 / Bank 0 · ROM A18 = 0';
  $('search').disabled = $('chips').disabled = $('confirm').disabled = busy;
  $('read').textContent = busy ? ($('cancel').disabled ? 'キャンセル中…' : '読み出し中…') : '↓ ROMを読み出す';
}
function clearPinout() {
  selectedProfile = null;
  $('pinout').hidden = true;
  $('pinoutGraphic').hidden = true;
  $('pins').replaceChildren();
  $('pinWarnings').replaceChildren();
  $('pinsLeft').replaceChildren();
  $('pinsRight').replaceChildren();
  $('profileAdapterConfirm').checked = false;
  $('profileDirectConfirm').checked = false;
  $('profileAdapterLabel').hidden = true;
  $('profileDirectLabel').hidden = true;
}
function graphicalPin(pin) {
  const item = document.createElement('div');
  item.className = `graphic-pin pin-${String(pin.role || '').toLowerCase()}`;
  item.dataset.pin = pin.pin;
  item.title = `${String(pin.pin).padStart(2, '0')} · ${pin.signal} · ${pin.note || ''}`;
  const label = document.createElement('span'); label.className = 'graphic-pin-label';
  const number = document.createElement('b'); number.className = 'graphic-pin-number'; number.textContent = String(pin.pin).padStart(2, '0');
  const signal = document.createElement('span'); signal.className = 'graphic-pin-signal'; signal.textContent = pin.signal;
  label.append(number, signal);
  const lead = document.createElement('i'); lead.className = 'graphic-pin-lead';
  if (item.classList.contains('pin-power')) lead.setAttribute('aria-label', '電源');
  item.append(label, lead);
  return item;
}
function renderGraphicalPinout(data) {
  if (!data.pins?.length) return;
  const split = Math.ceil(data.pins.length / 2);
  const left = data.pins.slice(0, split);
  const right = data.pins.slice(split).reverse();
  $('pinsLeft').replaceChildren(...left.map(graphicalPin));
  $('pinsRight').replaceChildren(...right.map(graphicalPin));
  $('graphicChipType').textContent = data.direct_supported ? 'CUSTOM PROM / DIRECT ZIF' : 'ADAPTER PROFILE';
  $('graphicChipName').textContent = data.name || 'ROM';
  $('graphicReader').textContent = data.direct_supported ? 'TL866CS · bit-bang' : `→ ${data.reader_chip || 'adapter'}`;
  $('pinoutPackage').textContent = data.name?.match(/DIP-?\d+/i)?.[0] || `${data.pins.length}-PIN`;
  $('pinoutMode').textContent = data.direct_supported ? 'DIRECT CUSTOM MAP' : 'ADAPTER MAP';
  $('pinoutGraphicNote').textContent = data.direct_supported ? '切り欠きを上に見たROM側の配置。TL866CSのカスタムPROMビットバンで、このピン番号を直接制御します。' : '切り欠きを上に見たROM側の配置。右側の図はROMの実ピン、読み出しは変換アダプター経由です。';
  $('pinoutGraphic').hidden = false;
}
function renderPinout(data) {
  clearPinout();
  if (!data.pins?.length) return;
  selectedProfile = {kind: data.profile, name: data.name, adapter_required: data.adapter_required === true, direct_supported: data.direct_supported === true, reader_chip: data.reader_chip};
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
  renderGraphicalPinout(data);
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
$('profileDirectConfirm').addEventListener('change', controls);
$('refresh').addEventListener('click', status);
function updateProgress(job) {
  if (job.data_cleared) {
    $('progressPanel').hidden = true;
    return;
  }
  const expected = Number(job.expected_size) || 0;
  const bytes = Number(job.bytes_read ?? job.partial_size) || 0;
  const state = job.status || 'idle';
  const visible = state !== 'idle' && (state !== 'error' || bytes > 0 || job.error);
  $('progressPanel').hidden = !visible;
  if (!visible) return;
  const percent = state === 'done' ? 100 : expected ? Math.min(99.9, Math.max(0, Number(job.progress) || bytes * 100 / expected)) : 0;
  $('progressFill').style.width = expected ? `${percent}%` : bytes ? '100%' : '0%';
  $('progressValue').textContent = expected ? `${Math.round(percent)}%` : bytes ? `${bytes.toLocaleString()} B` : '—';
  $('progressBytes').textContent = expected ? `${bytes.toLocaleString()} / ${expected.toLocaleString()} bytes` : `${bytes.toLocaleString()} bytes取得`;
  $('progressLabel').textContent = job.profile === 'sc88' ? `SC-88Pro · Bank ${job.bank === 1 ? 1 : 0}` : (job.chip || 'READING ROM');
  const speedLabel = ({fast: '高速', normal: '標準', slow: '低速・安定', safe: '最低速・検証用'})[job.speed] || job.speed || '標準';
  $('progressHint').textContent = state === 'done' ? '読み出し完了' :
    state === 'canceling' ? '停止処理中…' : state === 'canceled' ? 'キャンセル済み · 取得済みデータを保持' :
    state === 'error' ? 'エラー · 取得済みデータを保持' : state === 'waiting_bank' ? 'Bank 0取得済み · 次のバンク待ち' :
    `読み出し中 · ${speedLabel} · 取得済みデータを表示`;
}
async function hex(at = 0) {
  const d = await api('/api/hex?offset=' + at); offset = d.offset; total = d.size;
  $('hex').textContent = d.text; $('range').textContent = `${offset.toString(16).toUpperCase().padStart(8, '0')} / ${total.toLocaleString()} bytes`;
  $('prev').disabled = offset === 0; $('next').disabled = offset + 256 >= total;
  $('partialNote').hidden = !d.partial;
  $('partialNote').textContent = d.partial ? `途中ダンプ · ${total.toLocaleString()} bytes取得済み（読み出し中に更新）` : '';
  viewerHasData = true; controls();
}
$('prev').onclick = () => hex(Math.max(0, offset - 256)).catch(error);
$('next').onclick = () => hex(offset + 256).catch(error);
$('jump').onclick = () => { const value = $('offset').value.trim(); if (!/^(0x)?[0-9a-f]+$/i.test(value)) return error(new Error('16進数のアドレスを入力してください。')); const address = parseInt(value, 16); if (!Number.isSafeInteger(address) || address >= total) return error(new Error('ROMの容量内のアドレスを入力してください。')); hex(address).catch(error); };
$('download').onclick = async e => {
  e.preventDefault();
  try {
    const response = await fetch('/api/download', { cache: 'no-store' });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || `HTTP ${response.status}`);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    const sourceName = $('resultChip').textContent !== '—' ? $('resultChip').textContent : chip || 'rom';
    link.download = `${sourceName.replace(/[^A-Za-z0-9_.-]/g, '_')}.bin`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (e) { error(e); }
};
$('read').onclick = async () => {
  if ($('read').disabled) return;
  $('error').hidden = true; busy = true; previewStamp = ''; controls();
  try {
    const request = { chip, confirmed: $('confirm').checked, speed: $('readSpeed').value };
    if (selectedProfile?.direct_supported) request.profile_direct_confirmed = $('profileDirectConfirm').checked;
    else if (selectedProfile) request.profile_adapter_confirmed = $('profileAdapterConfirm').checked;
    await api('/api/read', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Session-Token': token }, body: JSON.stringify(request) });
    shownResult = null; viewerHasData = false; $('progressPanel').hidden = $('download').hidden = $('hexPanel').hidden = true; $('partialNote').hidden = true; $('empty').hidden = false;
    $('resultChip').textContent = chip; $('size').textContent = $('duration').textContent = '—';
    await poll();
  } catch (e) { busy = false; controls(); error(e); }
};
$('cancel').onclick = async () => {
  if ($('cancel').disabled) return;
  $('cancel').disabled = true; $('cancel').textContent = 'キャンセル処理中…';
  try {
    await api('/api/cancel', { method: 'POST', headers: { 'X-Session-Token': token } });
    await poll();
  } catch (e) { $('cancel').disabled = false; controls(); error(e); }
};
$('clear').onclick = async () => {
  if ($('clear').disabled) return;
  $('clear').disabled = true;
  try {
    await api('/api/clear', { method: 'POST', headers: { 'X-Session-Token': token } });
    viewerHasData = false; previewStamp = ''; shownResult = null;
    $('progressPanel').hidden = $('download').hidden = $('hexPanel').hidden = true;
    $('partialNote').hidden = true; $('empty').hidden = false;
    $('resultChip').textContent = $('size').textContent = $('duration').textContent = '—';
    $('range').textContent = ''; $('hex').textContent = ''; $('hash').textContent = '';
    await poll();
  } catch (e) { controls(); error(e); }
};
async function poll() {
  try {
    const job = await api('/api/job');
    if (job.profile === 'sc88') {
      if (scJob?.status !== job.status) $('adapterConfirm').checked = false;
      scJob = job;
      $('scProgress').textContent = job.status === 'waiting_bank' ? 'Bank 0取得済み。処理は停止中です。バンクを切り替えて再確認してください。' : job.status === 'done' ? '2 / 2 banks · 1 MiB結合済み' : job.status === 'error' ? '失敗。Bank 0からやり直してください。' : job.status === 'canceled' ? 'キャンセル済み。Bank 0からやり直してください。' : `Bank ${job.bank} 読み出し中`;
    } else scJob = null;
    busy = job.status === 'running' || job.status === 'canceling';
    $('jobStatus').dataset.state = job.status;
    $('jobStatus').textContent = ({ idle: '待機中', waiting_bank: 'Bank 1の切替待ち', running: '読み出し中…', canceling: 'キャンセル中…', canceled: 'キャンセル済み', done: '読み出し完了', error: '読み出し失敗' })[job.status] || job.status;
    updateProgress(job); controls();
    if (job.log) { $('log').textContent = job.log; $('log').scrollTop = $('log').scrollHeight; }
    if (job.status === 'error') error(new Error(job.error));
    if (job.data_cleared) {
      viewerHasData = false; shownResult = job.started || shownResult;
      $('empty').hidden = false; $('hexPanel').hidden = $('download').hidden = true;
    } else if (job.partial_size > 0 && job.status !== 'done') {
      const stamp = `${job.id}:${job.partial_size}`;
      if (stamp !== previewStamp) {
        await hex(0); previewStamp = stamp;
      }
      $('empty').hidden = true; $('hexPanel').hidden = false; $('download').hidden = true;
      $('resultChip').textContent = job.chip || chip; $('size').textContent = `${job.partial_size.toLocaleString()} B`;
    }
    if (job.status === 'done' && !job.data_cleared && shownResult !== job.started) {
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
  busy = true; previewStamp = ''; controls(); $('error').hidden = true;
  try {
    await api('/api/sc88/read', {method:'POST', headers:{'Content-Type':'application/json','X-Session-Token':token}, body:JSON.stringify({bank, job_id:scJob?.id, adapter_confirmed:$('adapterConfirm').checked, confirmed:true, speed:$('readSpeed').value})});
    shownResult = null; viewerHasData = false; $('adapterConfirm').checked = false;
    $('progressPanel').hidden = $('download').hidden = $('hexPanel').hidden = true; $('partialNote').hidden = true; $('empty').hidden = false;
    $('resultChip').textContent = 'SC-88Pro PRG ROM'; $('size').textContent = $('duration').textContent = '—';
    await poll();
  } catch(e) {busy = false; controls(); error(e);}
};
