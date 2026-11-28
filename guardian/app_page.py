"""
Multi-User SaaS Workspace Portal HTML for Flagsmith Guardian (/app).
Allows any user to sign up, log in, connect their own Flagsmith project,
configure rollout stages & guardrails in the UI, and monitor/control rollouts.
"""

APP_PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Guardian 🛡️ | Workspaces</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg-dark: #0b0f19;
      --card-bg: #111827;
      --card-border: #1f2937;
      --text-main: #f9fafb;
      --text-muted: #9ca3af;
      --accent-primary: #6366f1;
      --accent-success: #10b981;
      --accent-warning: #f59e0b;
      --accent-danger: #ef4444;
      --font-sans: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      --font-mono: 'JetBrains Mono', monospace;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg-dark);
      color: var(--text-main);
      font-family: var(--font-sans);
      line-height: 1.5;
      padding: 24px;
      -webkit-font-smoothing: antialiased;
    }
    .container { width: 100%; max-width: 1200px; margin: 0 auto; }
    header {
      display: flex; justify-content: space-between; align-items: center;
      flex-wrap: wrap; gap: 12px; margin-bottom: 24px; padding-bottom: 16px;
      border-bottom: 1px solid var(--card-border);
    }
    .logo { display: flex; align-items: center; gap: 10px; }
    .logo h1 { font-size: 20px; font-weight: 700; }
    .badge {
      background: rgba(99,102,241,0.15); color: #818cf8;
      font-size: 11px; font-weight: 600; padding: 2px 8px;
      border-radius: 4px; border: 1px solid rgba(99,102,241,0.3);
    }
    .card {
      background: var(--card-bg); border: 1px solid var(--card-border);
      border-radius: 12px; padding: 20px; margin-bottom: 20px;
    }
    .card-title {
      font-size: 12px; font-weight: 600; text-transform: uppercase;
      letter-spacing: 0.8px; color: var(--text-muted); margin-bottom: 14px;
      display: flex; justify-content: space-between; align-items: center;
    }
    .btn {
      padding: 9px 14px; border-radius: 8px; border: 1px solid var(--card-border);
      background: #1f2937; color: var(--text-main); font-size: 13px;
      font-weight: 600; cursor: pointer; font-family: inherit; transition: all 0.15s;
    }
    .btn:hover:not(:disabled) { border-color: #4b5563; background: #263244; }
    .btn:disabled { opacity: 0.45; cursor: not-allowed; }
    .btn-primary { background: var(--accent-primary); border-color: var(--accent-primary); color: #fff; }
    .btn-primary:hover:not(:disabled) { background: #4f46e5; }
    .btn-success { background: rgba(16,185,129,0.15); border-color: rgba(16,185,129,0.4); color: #34d399; }
    .btn-warning { background: rgba(245,158,11,0.15); border-color: rgba(245,158,11,0.4); color: #fbbf24; }
    .btn-danger { background: rgba(239,68,68,0.15); border-color: rgba(239,68,68,0.4); color: #f87171; }
    input, select {
      width: 100%; padding: 9px 12px; border-radius: 8px;
      border: 1px solid var(--card-border); background: #0f172a;
      color: var(--text-main); font-size: 13px; font-family: inherit;
    }
    input:focus, select:focus { outline: none; border-color: var(--accent-primary); }
    label { display: block; font-size: 12px; font-weight: 600; color: var(--text-muted); margin-bottom: 5px; }
    .field { margin-bottom: 14px; }
    .row2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    @media (max-width: 720px) { .row2 { grid-template-columns: 1fr; } }
    .auth-box { max-width: 440px; margin: 48px auto; }
    .tabs { display: flex; gap: 8px; margin-bottom: 18px; }
    .tab { flex: 1; text-align: center; padding: 8px; border-radius: 8px; cursor: pointer; font-size: 13px; font-weight: 600; background: #0f172a; color: var(--text-muted); border: 1px solid var(--card-border); }
    .tab.active { background: rgba(99,102,241,0.2); color: #a5b4fc; border-color: rgba(99,102,241,0.5); }
    .ws-bar { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 20px; }
    .ws-chip {
      padding: 7px 12px; border-radius: 8px; border: 1px solid var(--card-border);
      background: var(--card-bg); color: var(--text-muted); font-size: 13px; cursor: pointer;
    }
    .ws-chip.active { border-color: var(--accent-primary); color: #fff; background: rgba(99,102,241,0.16); }
    .grid { display: grid; grid-template-columns: 2fr 1fr; gap: 20px; }
    @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
    .stepper { display: flex; justify-content: space-between; align-items: center; gap: 8px; margin: 18px 0; position: relative; }
    .step { flex: 1; text-align: center; }
    .step-dot {
      width: 34px; height: 34px; border-radius: 50%; margin: 0 auto 6px;
      display: flex; align-items: center; justify-content: center; font-size: 11px; font-weight: 700;
      border: 2px solid #374151; background: var(--bg-dark); color: var(--text-muted);
    }
    .step.done .step-dot { border-color: var(--accent-success); background: rgba(16,185,129,0.2); color: #34d399; }
    .step.active .step-dot { border-color: var(--accent-primary); background: var(--accent-primary); color: #fff; }
    .step-label { font-size: 11px; color: var(--text-muted); font-family: var(--font-mono); }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th { text-align: left; font-size: 11px; color: var(--text-muted); text-transform: uppercase; padding: 8px; border-bottom: 1px solid var(--card-border); }
    td { padding: 10px 8px; border-bottom: 1px solid #161e2e; }
    .mono { font-family: var(--font-mono); font-size: 12px; }
    pre.snippet {
      background: #090d16; border: 1px solid var(--card-border); border-radius: 8px;
      padding: 12px; overflow-x: auto; font-family: var(--font-mono); font-size: 12px; color: #c7d2fe;
    }
    .pill {
      display: inline-block; padding: 3px 9px; border-radius: 999px; font-size: 11px; font-weight: 600;
      background: rgba(99,102,241,0.16); color: #a5b4fc; border: 1px solid rgba(99,102,241,0.3);
    }
    .pill.ok { background: rgba(16,185,129,0.16); color: #34d399; border-color: rgba(16,185,129,0.35); }
    .pill.warn { background: rgba(245,158,11,0.16); color: #fbbf24; border-color: rgba(245,158,11,0.35); }
    .pill.bad { background: rgba(239,68,68,0.16); color: #f87171; border-color: rgba(239,68,68,0.35); }
    .modal-backdrop {
      position: fixed; inset: 0; background: rgba(0,0,0,0.72); display: none;
      align-items: center; justify-content: center; padding: 16px; z-index: 50;
    }
    .modal-backdrop.open { display: flex; }
    .modal { width: 100%; max-width: 620px; max-height: 90vh; overflow-y: auto; }
    .msg { font-size: 13px; margin-top: 10px; color: #f87171; }
    .msg.ok { color: #34d399; }
    .hidden { display: none !important; }
  </style>
</head>
<body>
<div class="container">
  <header>
    <div class="logo">
      <h1>Guardian 🛡️</h1>
      <span class="badge">Multi-User Workspaces</span>
      <a href="/" style="color:var(--text-muted); font-size:12px; text-decoration:none; margin-left:8px;">← Single-Tenant Dashboard</a>
    </div>
    <div id="userControls" class="hidden" style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
      <span id="userEmailBadge" class="mono" style="color:var(--text-muted);"></span>
      <button class="btn btn-primary" onclick="openCreateModal()">+ New Rollout Workspace</button>
      <button class="btn" onclick="logout()">Sign Out</button>
    </div>
  </header>

  <!-- AUTH VIEW -->
  <div id="authView" class="card auth-box">
    <div class="tabs">
      <div id="tabLogin" class="tab active" onclick="switchAuthTab('login')">Log In</div>
      <div id="tabSignup" class="tab" onclick="switchAuthTab('signup')">Create Account</div>
    </div>
    <form id="authForm" onsubmit="submitAuth(event)">
      <div id="nameField" class="field hidden">
        <label>Your Name</label>
        <input id="authName" type="text" placeholder="Alex Rivera">
      </div>
      <div class="field">
        <label>Email</label>
        <input id="authEmail" type="email" required placeholder="you@company.com">
      </div>
      <div class="field">
        <label>Password (min 8 characters)</label>
        <input id="authPassword" type="password" required minlength="8" placeholder="••••••••">
      </div>
      <button id="authSubmitBtn" type="submit" class="btn btn-primary" style="width:100%;">Log In</button>
      <div id="authMsg" class="msg"></div>
    </form>
  </div>

  <!-- PORTAL VIEW -->
  <div id="portalView" class="hidden">
    <div id="wsBar" class="ws-bar"></div>

    <div id="emptyState" class="card hidden" style="text-align:center; padding:42px 20px;">
      <h2 style="margin-bottom:8px;">Welcome to Flagsmith Guardian</h2>
      <p style="color:var(--text-muted); margin-bottom:18px; max-width:520px; margin-left:auto; margin-right:auto;">
        Connect your Flagsmith project and guardrails to automatically roll out features safely and roll back in seconds if errors spike.
      </p>
      <button class="btn btn-primary" onclick="openCreateModal()">+ Connect Your First Flagsmith Flag</button>
    </div>

    <div id="wsDashboard" class="hidden">
      <div class="grid">
        <div class="card">
          <div class="card-title">
            <span>Active Rollout State</span>
            <span id="wsFlagPill" class="pill">--</span>
          </div>
          <div style="display:flex; justify-content:space-between; align-items:baseline; flex-wrap:wrap; gap:8px;">
            <div>
              <span id="wsStateTitle" style="font-size:26px; font-weight:700;">--</span>
              <span id="wsPctBadge" class="badge" style="margin-left:8px;">0%</span>
            </div>
            <div id="wsSoak" class="mono" style="color:var(--text-muted);">SOAK: --</div>
          </div>
          <div id="wsStepper" class="stepper"></div>
          <div id="wsOpPill" class="pill" style="margin-top:8px;">MONITORING</div>
        </div>

        <div class="card">
          <div class="card-title">Operator Actions</div>
          <div style="display:flex; flex-direction:column; gap:8px;">
            <button id="btnApprove" class="btn btn-success" onclick="wsAction('approve')">✅ Approve Final Promotion</button>
            <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px;">
              <button id="btnPause" class="btn btn-warning" onclick="wsAction('pause')">⏸ Pause</button>
              <button id="btnResume" class="btn" onclick="wsAction('resume')">▶ Resume</button>
            </div>
            <button class="btn btn-danger" onclick="wsAction('rollback')">🚨 Emergency Rollback to 0%</button>
            <button class="btn btn-primary" onclick="wsAction('tick')">⚡ Evaluate Guardrails Now</button>
            <button class="btn" onclick="wsAction('reset')">🔄 Reset Rollout to 0%</button>
          </div>
          <div id="actionMsg" class="msg"></div>
        </div>
      </div>

      <div class="card">
        <div class="card-title">
          <span>Push Metrics &amp; Integration</span>
          <div style="display:flex; gap:8px;">
            <button class="btn" onclick="rotateKey()">Rotate Ingest Key</button>
            <button class="btn btn-danger" onclick="deleteCurrentWs()">Delete Workspace</button>
          </div>
        </div>
        <p style="font-size:13px; color:var(--text-muted); margin-bottom:10px;">
          Send your application metrics to this workspace's isolated endpoint (or use Prometheus/Datadog/CloudWatch in workspace config):
        </p>
        <pre id="wsSnippet" class="snippet"></pre>
      </div>

      <div class="card">
        <div class="card-title">Guardrail Metrics</div>
        <table>
          <thead><tr><th>Guardrail</th><th>Source</th><th>Observed</th><th>Threshold</th><th>Samples</th><th>Status</th><th>Note</th></tr></thead>
          <tbody id="wsGuardrailsBody"></tbody>
        </table>
      </div>

      <div class="card">
        <div class="card-title">
          <span>Decision Timeline &amp; Audit Trail</span>
          <button class="btn" onclick="verifyAudit()">Verify SHA-256 Chain</button>
        </div>
        <div id="auditVerifyMsg" class="msg" style="margin-bottom:10px;"></div>
        <table>
          <thead><tr><th>Time</th><th>Decision</th><th>Transition</th><th>Reason</th></tr></thead>
          <tbody id="wsAuditBody"></tbody>
        </table>
      </div>
    </div>
  </div>
</div>

<!-- CREATE WORKSPACE MODAL -->
<div id="createModal" class="modal-backdrop">
  <div class="card modal">
    <div class="card-title">
      <span>Connect Flagsmith Rollout Workspace</span>
      <button class="btn" onclick="closeCreateModal()">✕</button>
    </div>
    <form onsubmit="createWorkspace(event)">
      <div class="row2">
        <div class="field">
          <label>Workspace Name</label>
          <input id="wsName" required placeholder="Production Checkout">
        </div>
        <div class="field">
          <label>Flagsmith Feature Flag Name</label>
          <input id="wsFlagName" required placeholder="new_checkout">
        </div>
      </div>
      <div class="field">
        <label>Flagsmith API URL</label>
        <input id="wsFlagsmithUrl" required value="https://app.flagsmith.com/api/v1">
      </div>
      <div class="row2">
        <div class="field">
          <label>Flagsmith Admin API Key</label>
          <input id="wsAdminKey" type="password" required placeholder="ser.xxxx...">
        </div>
        <div class="field">
          <label>Flagsmith Environment Key</label>
          <input id="wsEnvKey" required placeholder="env_xxxx...">
        </div>
      </div>
      <div class="row2">
        <div class="field">
          <label>Rollout Stages (%)</label>
          <input id="wsStages" value="1, 5, 25, 100">
        </div>
        <div class="field">
          <label>Soak Time per Stage (seconds)</label>
          <input id="wsSoakInput" type="number" min="5" value="30">
        </div>
      </div>
      <div class="row2">
        <div class="field">
          <label>Guardrail Metric Source</label>
          <select id="wsSource">
            <option value="push">Push API (/metrics/ingest)</option>
            <option value="prometheus">Prometheus</option>
            <option value="datadog">Datadog</option>
            <option value="cloudwatch">AWS CloudWatch</option>
          </select>
        </div>
        <div class="field">
          <label>Metric Name</label>
          <input id="wsMetricName" value="checkout">
        </div>
      </div>
      <div class="row2">
        <div class="field">
          <label>Max Error Threshold (%)</label>
          <input id="wsThreshold" type="number" step="0.1" value="5.0">
        </div>
        <div class="field">
          <label>Min Samples Required</label>
          <input id="wsMinSamples" type="number" min="1" value="30">
        </div>
      </div>
      <div class="field" style="display:flex; align-items:center; gap:8px;">
        <input id="wsReqApproval" type="checkbox" checked style="width:auto;">
        <label for="wsReqApproval" style="margin:0;">Require human approval before promoting to 100%</label>
      </div>
      <div id="createMsg" class="msg"></div>
      <div style="display:flex; justify-content:flex-end; gap:8px; margin-top:14px;">
        <button type="button" class="btn" onclick="closeCreateModal()">Cancel</button>
        <button type="submit" class="btn btn-primary">Create Workspace</button>
      </div>
    </form>
  </div>
</div>

<script>
  let authMode = 'login';
  let csrfToken = '';
  let workspaces = [];
  let activeWsId = null;
  let pollTimer = null;

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function switchAuthTab(mode) {
    authMode = mode;
    document.getElementById('tabLogin').classList.toggle('active', mode === 'login');
    document.getElementById('tabSignup').classList.toggle('active', mode === 'signup');
    document.getElementById('nameField').classList.toggle('hidden', mode === 'login');
    document.getElementById('authSubmitBtn').textContent = mode === 'login' ? 'Log In' : 'Create Account';
    document.getElementById('authMsg').textContent = '';
  }

  async function apiFetch(url, opts = {}) {
    const headers = Object.assign({}, opts.headers || {});
    if (opts.method && opts.method !== 'GET' && csrfToken) {
      headers['X-CSRF-Token'] = csrfToken;
    }
    if (opts.body && !headers['Content-Type']) {
      headers['Content-Type'] = 'application/json';
    }
    const res = await fetch(url, Object.assign({}, opts, { headers, credentials: 'same-origin' }));
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`);
    return data;
  }

  async function submitAuth(e) {
    e.preventDefault();
    const msg = document.getElementById('authMsg');
    msg.textContent = '';
    const email = document.getElementById('authEmail').value;
    const password = document.getElementById('authPassword').value;
    const name = document.getElementById('authName').value;
    try {
      const endpoint = authMode === 'login' ? '/api/auth/login' : '/api/auth/signup';
      const data = await apiFetch(endpoint, {
        method: 'POST',
        body: JSON.stringify(authMode === 'login' ? { email, password } : { email, password, name }),
      });
      csrfToken = data.csrf_token || '';
      showPortal(data.user);
    } catch (err) {
      msg.textContent = err.message;
    }
  }

  async function checkSession() {
    try {
      const data = await apiFetch('/api/auth/me');
      csrfToken = data.csrf_token || '';
      showPortal(data.user);
    } catch (_) {
      document.getElementById('authView').classList.remove('hidden');
      document.getElementById('portalView').classList.add('hidden');
      document.getElementById('userControls').classList.add('hidden');
    }
  }

  async function logout() {
    if (pollTimer) clearInterval(pollTimer);
    await apiFetch('/api/auth/logout', { method: 'POST' }).catch(() => {});
    csrfToken = '';
    workspaces = [];
    activeWsId = null;
    document.getElementById('authView').classList.remove('hidden');
    document.getElementById('portalView').classList.add('hidden');
    document.getElementById('userControls').classList.add('hidden');
  }

  async function showPortal(user) {
    document.getElementById('authView').classList.add('hidden');
    document.getElementById('portalView').classList.remove('hidden');
    document.getElementById('userControls').classList.remove('hidden');
    document.getElementById('userEmailBadge').textContent = user.email;
    await loadWorkspaces();
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(() => { if (activeWsId) refreshActiveWorkspace(); }, 3000);
  }

  async function loadWorkspaces() {
    const data = await apiFetch('/api/workspaces');
    workspaces = data.workspaces || [];
    const bar = document.getElementById('wsBar');
    bar.innerHTML = workspaces.map(w =>
      `<div class="ws-chip ${w.id === activeWsId ? 'active' : ''}" onclick="selectWorkspace('${esc(w.id)}')">
        <strong>${esc(w.name)}</strong> <span class="mono" style="opacity:0.7;">(${esc(w.flag_name)})</span>
      </div>`
    ).join('');
    if (workspaces.length === 0) {
      activeWsId = null;
      document.getElementById('emptyState').classList.remove('hidden');
      document.getElementById('wsDashboard').classList.add('hidden');
      return;
    }
    document.getElementById('emptyState').classList.add('hidden');
    document.getElementById('wsDashboard').classList.remove('hidden');
    if (!activeWsId || !workspaces.find(w => w.id === activeWsId)) {
      activeWsId = workspaces[0].id;
    }
    await refreshActiveWorkspace();
    // Re-render active chip
    bar.innerHTML = workspaces.map(w =>
      `<div class="ws-chip ${w.id === activeWsId ? 'active' : ''}" onclick="selectWorkspace('${esc(w.id)}')">
        <strong>${esc(w.name)}</strong> <span class="mono" style="opacity:0.7;">(${esc(w.flag_name)})</span>
      </div>`
    ).join('');
  }

  async function selectWorkspace(id) {
    activeWsId = id;
    await loadWorkspaces();
  }

  async function refreshActiveWorkspace() {
    if (!activeWsId) return;
    const ws = workspaces.find(w => w.id === activeWsId);
    if (!ws) return;
    const st = await apiFetch(`/api/workspaces/${encodeURIComponent(activeWsId)}/status`);
    const rollouts = st.rollouts || {};
    const flagStatus = rollouts[ws.flag_name] || Object.values(rollouts)[0] || st;

    document.getElementById('wsFlagPill').textContent = `Flag: ${ws.flag_name}`;
    document.getElementById('wsStateTitle').textContent = flagStatus.state || 'PENDING';
    document.getElementById('wsPctBadge').textContent = `${flagStatus.percentage ?? 0}% Allocation`;
    document.getElementById('wsSoak').textContent = `SOAK: ${(flagStatus.soak_elapsed ?? 0).toFixed(1)}s / ${ws.soak_time_seconds}s`;

    const opPill = document.getElementById('wsOpPill');
    const opStatus = flagStatus.operational_status || 'Monitoring';
    opPill.textContent = opStatus;
    opPill.className = 'pill ' + (opStatus.includes('Healthy') ? 'ok' : (opStatus.includes('Rollback') || opStatus.includes('breach') ? 'bad' : 'warn'));

    const stages = ws.stages || [1, 5, 25, 100];
    const currentPct = Number(flagStatus.percentage ?? 0);
    document.getElementById('wsStepper').innerHTML = [0, ...stages].map(pct => {
      const cls = currentPct > pct ? 'done' : (currentPct === pct ? 'active' : '');
      return `<div class="step ${cls}"><div class="step-dot">${pct}%</div><div class="step-label">${pct === 0 ? 'IDLE' : pct + '%'}</div></div>`;
    }).join('');

    const metricName = (ws.guardrails && ws.guardrails[0] && ws.guardrails[0].metric) || 'checkout';
    const origin = window.location.origin;
    document.getElementById('wsSnippet').textContent =
`curl -X POST ${origin}/api/workspaces/${ws.id}/metrics/ingest \\
  -H "Authorization: Bearer ${ws.ingest_api_key}" \\
  -H "Content-Type: application/json" \\
  -d '{"metric":"${metricName}","requests":50,"errors":1}'`;

    const grs = flagStatus.guardrails || [];
    document.getElementById('wsGuardrailsBody').innerHTML = grs.map(g =>
      `<tr>
        <td class="mono">${esc(g.name)}</td>
        <td class="mono">${esc(g.metric_source)}</td>
        <td class="mono">${g.observed_value == null ? '--' : esc(g.observed_value)}</td>
        <td class="mono">${esc(g.comparison)} ${esc(g.threshold)}</td>
        <td class="mono">${esc(g.sample_size ?? 0)}</td>
        <td><span class="pill ${g.status === 'HEALTHY' ? 'ok' : (g.status === 'BREACH' ? 'bad' : 'warn')}">${esc(g.status)}</span></td>
        <td style="color:var(--text-muted);">${esc(g.message || '')}</td>
      </tr>`
    ).join('') || '<tr><td colspan="7" style="color:var(--text-muted);">No guardrails evaluated yet.</td></tr>';

    const logs = (flagStatus.audit_log || st.audit_log || []).slice(0, 10);
    document.getElementById('wsAuditBody').innerHTML = logs.map(a =>
      `<tr>
        <td class="mono">${esc(a.timestamp || '')}</td>
        <td><span class="pill">${esc(a.decision || '')}</span></td>
        <td class="mono">${esc(a.from_state)} → ${esc(a.to_state)}</td>
        <td>${esc(a.reason || '')}</td>
      </tr>`
    ).join('') || '<tr><td colspan="4" style="color:var(--text-muted);">No audit entries yet.</td></tr>';
  }

  async function wsAction(action) {
    if (!activeWsId) return;
    const msg = document.getElementById('actionMsg');
    msg.textContent = '';
    try {
      await apiFetch(`/api/workspaces/${encodeURIComponent(activeWsId)}/${action}`, {
        method: 'POST',
        body: JSON.stringify({}),
      });
      msg.className = 'msg ok';
      msg.textContent = `Action "${action}" executed.`;
      await refreshActiveWorkspace();
    } catch (err) {
      msg.className = 'msg';
      msg.textContent = err.message;
    }
  }

  async function verifyAudit() {
    if (!activeWsId) return;
    const el = document.getElementById('auditVerifyMsg');
    try {
      const res = await apiFetch(`/api/workspaces/${encodeURIComponent(activeWsId)}/audit/verify`);
      el.className = res.valid ? 'msg ok' : 'msg';
      el.textContent = res.valid
        ? `✅ Audit chain valid (${res.checked_entries} entries verified)`
        : `❌ Audit chain invalid: ${res.error || 'mismatch'}`;
    } catch (err) {
      el.className = 'msg';
      el.textContent = err.message;
    }
  }

  async function rotateKey() {
    if (!activeWsId) return;
    await apiFetch(`/api/workspaces/${encodeURIComponent(activeWsId)}/rotate-key`, { method: 'POST', body: '{}' });
    await loadWorkspaces();
  }

  async function deleteCurrentWs() {
    if (!activeWsId || !confirm('Delete this workspace and its audit history?')) return;
    await apiFetch(`/api/workspaces/${encodeURIComponent(activeWsId)}`, { method: 'DELETE' });
    activeWsId = null;
    await loadWorkspaces();
  }

  function openCreateModal() {
    document.getElementById('createMsg').textContent = '';
    document.getElementById('createModal').classList.add('open');
  }
  function closeCreateModal() {
    document.getElementById('createModal').classList.remove('open');
  }

  async function createWorkspace(e) {
    e.preventDefault();
    const msg = document.getElementById('createMsg');
    msg.textContent = '';
    try {
      const stages = document.getElementById('wsStages').value.split(',').map(s => parseInt(s.trim(), 10)).filter(n => !isNaN(n));
      const source = document.getElementById('wsSource').value;
      const metric = document.getElementById('wsMetricName').value.trim() || 'checkout';
      const threshold = parseFloat(document.getElementById('wsThreshold').value) || 5.0;
      const minSamples = parseInt(document.getElementById('wsMinSamples').value, 10) || 30;
      const payload = {
        name: document.getElementById('wsName').value,
        flag_name: document.getElementById('wsFlagName').value,
        flagsmith_url: document.getElementById('wsFlagsmithUrl').value,
        flagsmith_admin_api_key: document.getElementById('wsAdminKey').value,
        flagsmith_environment_key: document.getElementById('wsEnvKey').value,
        stages: stages.length ? stages : [1, 5, 25, 100],
        soak_time_seconds: parseInt(document.getElementById('wsSoakInput').value, 10) || 30,
        require_approval_before_final: document.getElementById('wsReqApproval').checked,
        guardrails: [{
          name: `${metric}_guardrail`,
          metric_source: source,
          metric,
          comparison: '>',
          threshold,
          min_samples: minSamples,
          window_seconds: 60,
          description: `Rollback to 0% if ${metric} exceeds ${threshold}%`,
        }],
      };
      const res = await apiFetch('/api/workspaces', { method: 'POST', body: JSON.stringify(payload) });
      activeWsId = res.workspace.id;
      closeCreateModal();
      await loadWorkspaces();
    } catch (err) {
      msg.textContent = err.message;
    }
  }

  checkSession();
</script>
</body>
</html>
"""
