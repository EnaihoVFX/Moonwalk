function socketLabel(state) {
  const labels = {
    0: 'CONNECTING',
    1: 'OPEN',
    2: 'CLOSING',
    3: 'CLOSED',
  };
  return labels[state] || String(state);
}

function setText(id, value, className = '') {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = value ?? '—';
  el.className = `value ${className}`.trim();
}

function renderStatus(status) {
  const ok = !!status?.authenticated;
  setText('bridgeStatus', ok ? 'Connected' : 'Disconnected', ok ? 'ok' : 'bad');
  setText('sessionId', status?.sessionId || '—');
  setText('socketState', socketLabel(status?.socketState));
  setText('lastMessage', status?.lastBridgeMessage || '—');
  setText('lastError', status?.lastBridgeError || '—', status?.lastBridgeError ? 'bad' : '');
  setText('snapshotCount', String(status?.snapshotCount ?? 0));

  const snapshot = status?.latestSnapshot || null;
  setText('latestUrl', snapshot?.url || '—');
  setText('latestTitle', snapshot?.title || '—');
  setText('elementCount', snapshot?.elements ? String(snapshot.elements.length) : '—');

  const meta = document.getElementById('latestMeta');
  if (meta) {
    if (!snapshot) {
      meta.textContent = 'No snapshot yet.';
    } else {
      meta.textContent = [
        `generation=${snapshot.generation}`,
        `tab_id=${snapshot.tab_id}`,
        `session_id=${snapshot.session_id}`,
      ].join('\n');
    }
  }
}

async function loadStatus() {
  const status = await chrome.runtime.sendMessage({ type: 'moonwalk_get_status' });
  renderStatus(status);
}

async function requestSnapshot() {
  const status = await chrome.runtime.sendMessage({ type: 'moonwalk_request_snapshot' });
  renderStatus(status);
}

document.getElementById('refreshBtn').addEventListener('click', loadStatus);
document.getElementById('snapshotBtn').addEventListener('click', requestSnapshot);

document.addEventListener('DOMContentLoaded', loadStatus);
