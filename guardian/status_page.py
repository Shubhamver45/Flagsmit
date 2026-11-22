"""
Guardian Status Page HTML Generator.
Provides a clean, self-contained, real-time dashboard served directly by Guardian.
"""

from __future__ import annotations


STATUS_PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Guardian 🛡️ | Flagsmith Guarded Rollouts</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #0b0f19;
      --card-bg: #111827;
      --card-border: #1f2937;
      --text: #f9fafb;
      --text-muted: #9ca3af;
      --accent: #6366f1;
      --accent-hover: #4f46e5;
      --success: #10b981;
      --danger: #ef4444;
      --warning: #f59e0b;
      --info: #3b82f6;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg);
      color: var(--text);
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      line-height: 1.5;
      padding: 24px;
    }
    .container { max-width: 1200px; margin: 0 auto; }
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 24px;
      padding-bottom: 16px;
      border-bottom: 1px solid var(--card-border);
    }
    .logo { display: flex; align-items: center; gap: 12px; }
    .logo h1 { font-size: 22px; font-weight: 700; letter-spacing: -0.5px; }
    .logo span {
      background: rgba(99, 102, 241, 0.15);
      color: #818cf8;
      font-size: 12px;
      padding: 3px 8px;
      border-radius: 9999px;
      font-weight: 600;
      border: 1px solid rgba(99, 102, 241, 0.3);
    }
    .refresh-badge {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 12px;
      color: var(--text-muted);
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      padding: 6px 12px;
      border-radius: 8px;
    }
    .pulse-dot {
      width: 8px;
      height: 8px;
      background: var(--success);
      border-radius: 50%;
      animation: pulse 2s infinite;
    }
    @keyframes pulse {
      0% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7); }
      70% { transform: scale(1); box-shadow: 0 0 0 6px rgba(16, 185, 129, 0); }
      100% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); }
    }
    
    .grid { display: grid; grid-template-columns: 2fr 1fr; gap: 20px; margin-bottom: 24px; }
    @media (max-width: 860px) { .grid { grid-template-columns: 1fr; } }
    
    .card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      padding: 20px;
    }
    .card-title {
      font-size: 14px;
      font-weight: 600;
      color: var(--text-muted);
      text-transform: uppercase;
      letter-spacing: 0.5px;
      margin-bottom: 12px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .metric-hero {
      display: flex;
      align-items: baseline;
      gap: 12px;
      margin-bottom: 16px;
    }
    .state-badge {
      font-size: 26px;
      font-weight: 700;
      letter-spacing: -0.5px;
    }
    .pct-badge {
      font-size: 18px;
      font-weight: 600;
      color: #818cf8;
      background: rgba(99, 102, 241, 0.15);
      padding: 4px 12px;
      border-radius: 8px;
    }
    
    /* Stepper */
    .stepper {
      display: flex;
      justify-content: space-between;
      position: relative;
      margin: 24px 0 8px;
    }
    .stepper::before {
      content: '';
      position: absolute;
      top: 14px;
      left: 10px;
      right: 10px;
      height: 2px;
      background: var(--card-border);
      z-index: 1;
    }
    .step-item {
      position: relative;
      z-index: 2;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 6px;
    }
    .step-circle {
      width: 30px;
      height: 30px;
      border-radius: 50%;
      background: var(--card-bg);
      border: 2px solid var(--card-border);
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 11px;
      font-weight: 600;
      color: var(--text-muted);
      transition: all 0.3s ease;
    }
    .step-item.active .step-circle {
      border-color: var(--accent);
      background: var(--accent);
      color: #fff;
      box-shadow: 0 0 12px rgba(99, 102, 241, 0.5);
    }
    .step-item.completed .step-circle {
      border-color: var(--success);
      background: var(--success);
      color: #fff;
    }
    .step-item.rollback .step-circle {
      border-color: var(--danger);
      background: var(--danger);
      color: #fff;
    }
    .step-label { font-size: 12px; font-weight: 500; color: var(--text-muted); }
    .step-item.active .step-label { color: var(--text); font-weight: 600; }

    /* Controls */
    .actions-panel {
      display: flex;
      flex-direction: column;
      gap: 12px;
      justify-content: center;
    }
    button {
      cursor: pointer;
      font-family: inherit;
      font-size: 14px;
      font-weight: 600;
      padding: 10px 16px;
      border-radius: 8px;
      border: none;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      transition: all 0.2s ease;
    }
    .btn-approve {
      background: var(--success);
      color: white;
    }
    .btn-approve:hover:not(:disabled) {
      background: #059669;
    }
    .btn-approve:disabled {
      background: #1f2937;
      color: #4b5563;
      cursor: not-allowed;
      border: 1px solid #374151;
    }
    .btn-rollback {
      background: rgba(239, 68, 68, 0.15);
      color: #f87171;
      border: 1px solid rgba(239, 68, 68, 0.3);
    }
    .btn-rollback:hover {
      background: rgba(239, 68, 68, 0.25);
    }
    .btn-tick {
      background: rgba(99, 102, 241, 0.15);
      color: #818cf8;
      border: 1px solid rgba(99, 102, 241, 0.3);
    }
    .btn-tick:hover {
      background: rgba(99, 102, 241, 0.25);
    }

    /* Tables */
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }
    th {
      text-align: left;
      padding: 10px 12px;
      color: var(--text-muted);
      border-bottom: 1px solid var(--card-border);
      font-weight: 600;
      text-transform: uppercase;
      font-size: 11px;
      letter-spacing: 0.5px;
    }
    td {
      padding: 12px;
      border-bottom: 1px solid rgba(31, 41, 55, 0.6);
      vertical-align: top;
    }
    tr:last-child td { border-bottom: none; }
    
    .status-pill {
      display: inline-flex;
      align-items: center;
      padding: 2px 8px;
      border-radius: 9999px;
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
    }
    .pill-healthy { background: rgba(16, 185, 129, 0.15); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.3); }
    .pill-breached { background: rgba(239, 68, 68, 0.15); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.3); }
    .pill-insufficient { background: rgba(245, 158, 11, 0.15); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.3); }
    .pill-advance { background: rgba(59, 130, 246, 0.15); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.3); }
    .pill-hold { background: rgba(156, 163, 175, 0.15); color: #9ca3af; border: 1px solid rgba(156, 163, 175, 0.3); }
    .pill-rollback { background: rgba(239, 68, 68, 0.15); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.3); }
    .pill-promoted { background: rgba(16, 185, 129, 0.15); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.3); }
    
    .mono { font-family: 'JetBrains Mono', monospace; font-size: 12px; }
    .banner-awaiting {
      background: rgba(245, 158, 11, 0.15);
      border: 1px solid rgba(245, 158, 11, 0.4);
      color: #fde68a;
      padding: 12px 16px;
      border-radius: 8px;
      margin-bottom: 16px;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="logo">
        <h1>Guardian 🛡️</h1>
        <span id="flag-badge">Flagsmith Flag: new_checkout</span>
      </div>
      <div class="refresh-badge">
        <span class="pulse-dot"></span>
        <span>Live Telemetry (Polling 2s)</span>
      </div>
    </header>

    <div id="awaiting-banner" class="banner-awaiting" style="display: none;">
      <div>
        <strong>Human Approval Required:</strong> All guardrails satisfied. Ready for final 100% promotion.
      </div>
      <button class="btn-approve" onclick="handleApprove()">Approve 100%</button>
    </div>

    <div class="grid">
      <!-- Status Card -->
      <div class="card">
        <div class="card-title">
          <span>Active Rollout State</span>
          <span id="soak-info" class="mono" style="font-size: 12px; color: var(--text-muted);">Soak: -- / --</span>
        </div>
        <div class="metric-hero">
          <div id="state-display" class="state-badge">PENDING</div>
          <div id="pct-display" class="pct-badge">0% Allocation</div>
        </div>

        <!-- Stepper -->
        <div id="stepper-container" class="stepper">
          <!-- Dynamic Steps -->
        </div>
      </div>

      <!-- Actions Panel -->
      <div class="card">
        <div class="card-title">Operator Actions</div>
        <div class="actions-panel">
          <button id="btn-approve-action" class="btn-approve" onclick="handleApprove()" disabled>
            ✅ Approve Final Promotion
          </button>
          <button class="btn-rollback" onclick="handleRollback()">
            🛑 Emergency Rollback to 0%
          </button>
          <button class="btn-tick" onclick="handleTick()">
            ⚡ Evaluate Guardrails Now
          </button>
          <button class="btn-tick" onclick="handleReset()" style="color: #9ca3af; border-color: rgba(156, 163, 175, 0.3);">
            🔄 Reset Rollout to 0%
          </button>
        </div>
      </div>
    </div>

    <!-- Guardrails -->
    <div class="card" style="margin-bottom: 24px;">
      <div class="card-title">Guardrail Metrics</div>
      <table>
        <thead>
          <tr>
            <th>Guardrail Name</th>
            <th>Source</th>
            <th>Observed Value</th>
            <th>Threshold</th>
            <th>Sample Size</th>
            <th>Status</th>
            <th>Evaluation Note</th>
          </tr>
        </thead>
        <tbody id="guardrails-body">
          <tr><td colspan="7" style="text-align: center; color: var(--text-muted);">Loading metrics...</td></tr>
        </tbody>
      </table>
    </div>

    <!-- Timeline -->
    <div class="card">
      <div class="card-title">Decision Timeline & Audit Trail</div>
      <table>
        <thead>
          <tr>
            <th>Timestamp</th>
            <th>Transition</th>
            <th>Decision</th>
            <th>Reason & Numerical Evidence</th>
          </tr>
        </thead>
        <tbody id="timeline-body">
          <tr><td colspan="4" style="text-align: center; color: var(--text-muted);">Loading audit timeline...</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <script>
    async function fetchStatus() {
      try {
        const res = await fetch('/status');
        if (!res.ok) return;
        const data = await res.json();
        renderDashboard(data);
      } catch (err) {
        console.error('Failed to fetch status:', err);
      }
    }

    function renderDashboard(data) {
      document.getElementById('state-display').innerText = data.state;
      document.getElementById('pct-display').innerText = data.percentage + '% Allocation';
      document.getElementById('flag-badge').innerText = 'Flagsmith Flag: ' + data.flag_name;

      if (data.soak_time_seconds) {
        document.getElementById('soak-info').innerText = `Soak: ${data.soak_elapsed_seconds || 0}s / ${data.soak_time_seconds}s`;
      }

      // Banner & Approve button
      const isAwaiting = data.state === 'AWAITING_APPROVAL';
      document.getElementById('awaiting-banner').style.display = isAwaiting ? 'flex' : 'none';
      document.getElementById('btn-approve-action').disabled = !isAwaiting;

      // Color code state badge
      const stateEl = document.getElementById('state-display');
      if (data.state === 'PROMOTED') stateEl.style.color = '#34d399';
      else if (data.state === 'ROLLED_BACK') stateEl.style.color = '#f87171';
      else if (data.state === 'AWAITING_APPROVAL') stateEl.style.color = '#fbbf24';
      else stateEl.style.color = '#f9fafb';

      // Stepper
      renderStepper(data);

      // Guardrails
      renderGuardrails(data.guardrails || []);

      // Timeline
      renderTimeline(data.timeline || data.recent_audit_entries || []);
    }

    function renderStepper(data) {
      const stages = data.stages || [1, 5, 25, 100];
      const steps = [
        { label: '0%', state: 'PENDING' },
        ...stages.slice(0, -1).map((pct, idx) => ({ label: pct + '%', state: 'STAGE_' + (idx + 1) })),
        { label: 'Approval', state: 'AWAITING_APPROVAL' },
        { label: stages[stages.length - 1] + '%', state: 'PROMOTED' },
      ];

      const container = document.getElementById('stepper-container');
      container.innerHTML = '';

      steps.forEach((s) => {
        const item = document.createElement('div');
        item.className = 'step-item';

        if (data.state === 'ROLLED_BACK') {
          item.classList.add('rollback');
        } else if (data.state === s.state) {
          item.classList.add('active');
        } else if (data.state === 'PROMOTED') {
          item.classList.add('completed');
        } else if (s.state === 'PENDING' && data.state !== 'PENDING') {
          item.classList.add('completed');
        }

        item.innerHTML = `
          <div class="step-circle">${s.label}</div>
          <div class="step-label">${s.state}</div>
        `;
        container.appendChild(item);
      });
    }

    function renderGuardrails(guardrails) {
      const tbody = document.getElementById('guardrails-body');
      if (!guardrails.length) {
        tbody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: var(--text-muted);">No guardrails configured</td></tr>';
        return;
      }

      tbody.innerHTML = guardrails.map(g => {
        let pillClass = 'pill-hold';
        let pillText = g.status || 'UNKNOWN';

        if (g.is_breached || g.status === 'BREACHED') {
          pillClass = 'pill-breached';
          pillText = 'BREACHED';
        } else if (g.status === 'HEALTHY') {
          pillClass = 'pill-healthy';
          pillText = 'HEALTHY';
        } else if (g.status === 'INSUFFICIENT_DATA') {
          pillClass = 'pill-insufficient';
          pillText = 'LOW SAMPLES';
        }

        const val = g.value !== null && g.value !== undefined ? (typeof g.value === 'number' ? g.value.toFixed(2) + '%' : g.value) : '--';
        const samples = g.sample_size !== null && g.sample_size !== undefined ? g.sample_size : '--';
        const threshold = g.threshold !== undefined ? `${g.comparison || '<'} ${g.threshold}%` : '--';

        return `
          <tr>
            <td><strong>${g.name}</strong></td>
            <td><span class="mono">${g.metric_source || 'prometheus'}</span></td>
            <td><strong class="mono">${val}</strong></td>
            <td class="mono">${threshold}</td>
            <td class="mono">${samples}</td>
            <td><span class="status-pill ${pillClass}">${pillText}</span></td>
            <td style="color: var(--text-muted); font-size: 12px;">${g.reason || 'Monitoring healthy metrics'}</td>
          </tr>
        `;
      }).join('');
    }

    function renderTimeline(timeline) {
      const tbody = document.getElementById('timeline-body');
      if (!timeline.length) {
        tbody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: var(--text-muted);">No decisions recorded yet</td></tr>';
        return;
      }

      tbody.innerHTML = timeline.map(entry => {
        let decPill = 'pill-hold';
        if (entry.decision === 'ADVANCE') decPill = 'pill-advance';
        else if (entry.decision === 'ROLLBACK') decPill = 'pill-rollback';
        else if (entry.decision === 'PROMOTED') decPill = 'pill-promoted';
        else if (entry.decision === 'AWAIT_APPROVAL') decPill = 'pill-insufficient';

        const timeStr = entry.timestamp ? new Date(entry.timestamp * 1000).toLocaleTimeString() : '--';
        const transition = `${entry.from_state} ➔ <strong>${entry.to_state}</strong>`;

        return `
          <tr>
            <td class="mono" style="color: var(--text-muted); white-space: nowrap;">${timeStr}</td>
            <td>${transition}</td>
            <td><span class="status-pill ${decPill}">${entry.decision}</span></td>
            <td>
              <div>${entry.reason}</div>
              ${entry.numbers ? `<pre class="mono" style="font-size: 11px; color: var(--text-muted); margin-top: 4px; overflow-x: auto;">${JSON.stringify(entry.numbers)}</pre>` : ''}
            </td>
          </tr>
        `;
      }).join('');
    }

    function getAuthHeaders() {
      const headers = { 'Content-Type': 'application/json' };
      const token = localStorage.getItem('guardian_token');
      if (token) {
        headers['Authorization'] = 'Bearer ' + token;
      }
      return headers;
    }

    async function authenticatedFetch(url, options = {}) {
      options.headers = Object.assign(getAuthHeaders(), options.headers || {});
      let res = await fetch(url, options);
      if (res.status === 401) {
        const token = prompt('API token required for Guardian write actions. Enter token:');
        if (token) {
          localStorage.setItem('guardian_token', token.trim());
          options.headers['Authorization'] = 'Bearer ' + token.trim();
          res = await fetch(url, options);
        }
      }
      return res;
    }

    async function handleApprove() {
      if (!confirm('Approve final promotion to 100% rollout?')) return;
      try {
        const res = await authenticatedFetch('/approve', {
          method: 'POST',
          body: JSON.stringify({ approved_by: 'operator_ui', reason: 'Approved from Status Page' })
        });
        const data = await res.json();
        if (!res.ok) alert('Approval failed: ' + (data.detail || JSON.stringify(data)));
        else fetchStatus();
      } catch (err) {
        alert('Network error: ' + err);
      }
    }

    async function handleRollback() {
      const reason = prompt('Enter rollback reason:', 'Manual rollback from Status Page');
      if (reason === null) return;
      try {
        const res = await authenticatedFetch('/rollback', {
          method: 'POST',
          body: JSON.stringify({ reason })
        });
        const data = await res.json();
        if (!res.ok) alert('Rollback failed: ' + (data.detail || JSON.stringify(data)));
        else fetchStatus();
      } catch (err) {
        alert('Network error: ' + err);
      }
    }

    async function handleTick() {
      try {
        await authenticatedFetch('/tick', { method: 'POST' });
        fetchStatus();
      } catch (err) {
        console.error(err);
      }
    }

    async function handleReset() {
      if (!confirm('Reset rollout back to PENDING (0%) for a fresh demo run?')) return;
      try {
        await authenticatedFetch('/reset', { method: 'POST' });
        try { await fetch('http://localhost:5001/reset-metrics', { method: 'POST' }); } catch (e) {}
        fetchStatus();
      } catch (err) {
        alert('Reset error: ' + err);
      }
    }

    // Initial fetch and 2-second interval
    fetchStatus();
    setInterval(fetchStatus, 2000);
  </script>
</body>
</html>
"""
