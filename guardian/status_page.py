"""
Interactive Real-Time Guardian Status Page HTML.
Serves a self-contained single-page dashboard displaying:
- Multi-rollout support (one card per configured flag)
- Active rollout state, percentage, soak countdown, and visual stage stepper
- Startup reconciliation status and high-visibility DRIFT WARNING alerts
- Guardrail metrics with real-time values, thresholds, and health status
- Flagsmith experimentation results card (provenance line, Bayesian lift, chance-to-be-worse, SRM p-value)
- Live decision timeline and audit trail with numerical evidence
- Operator action controls (Approve, Rollback, Reset, Tick)
"""

STATUS_PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Guardian 🛡️ | Automated Guarded Rollouts</title>
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
      background-color: var(--bg-dark);
      color: var(--text-main);
      font-family: var(--font-sans);
      line-height: 1.5;
      padding: 24px;
      -webkit-font-smoothing: antialiased;
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
    .logo {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .logo h1 { font-size: 20px; font-weight: 700; letter-spacing: -0.5px; }
    .logo span {
      background: rgba(99, 102, 241, 0.15);
      color: #818cf8;
      font-size: 11px;
      font-weight: 600;
      padding: 2px 8px;
      border-radius: 4px;
      border: 1px solid rgba(99, 102, 241, 0.3);
    }
    .refresh-badge {
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 12px;
      color: var(--text-muted);
    }
    .pulse-dot {
      width: 8px;
      height: 8px;
      background-color: var(--accent-success);
      border-radius: 50%;
      box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7);
      animation: pulse 2s infinite cubic-bezier(0.66, 0, 0, 1);
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
      border-color: var(--accent-primary);
      background: var(--accent-primary);
      color: white;
      box-shadow: 0 0 12px rgba(99, 102, 241, 0.5);
    }
    .step-item.completed .step-circle {
      border-color: var(--accent-success);
      background: var(--accent-success);
      color: white;
    }
    .step-item.rollback .step-circle {
      border-color: var(--accent-danger);
      background: var(--accent-danger);
      color: white;
    }
    .step-label {
      font-size: 11px;
      font-weight: 500;
      color: var(--text-muted);
    }
    .step-item.active .step-label { color: var(--text-main); font-weight: 600; }
    
    /* Actions Panel */
    .actions-panel {
      display: flex;
      flex-direction: column;
      gap: 10px;
      margin-top: 12px;
    }
    button {
      font-family: var(--font-sans);
      font-size: 13px;
      font-weight: 600;
      padding: 10px 16px;
      border-radius: 8px;
      cursor: pointer;
      transition: all 0.2s ease;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      border: 1px solid transparent;
    }
    .btn-approve {
      background: rgba(16, 185, 129, 0.15);
      border-color: rgba(16, 185, 129, 0.4);
      color: #34d399;
    }
    .btn-approve:hover:not(:disabled) {
      background: #10b981;
      color: white;
      border-color: #10b981;
    }
    .btn-rollback {
      background: rgba(239, 68, 68, 0.15);
      border-color: rgba(239, 68, 68, 0.4);
      color: #f87171;
    }
    .btn-rollback:hover:not(:disabled) {
      background: #ef4444;
      color: white;
      border-color: #ef4444;
    }
    .btn-tick {
      background: rgba(99, 102, 241, 0.15);
      border-color: rgba(99, 102, 241, 0.4);
      color: #818cf8;
    }
    .btn-tick:hover:not(:disabled) {
      background: #6366f1;
      color: white;
      border-color: #6366f1;
    }
    button:disabled {
      opacity: 0.4;
      cursor: not-allowed;
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

    <!-- Drift Warning Banner -->
    <div id="drift-banner" style="display: none; background: rgba(239, 68, 68, 0.15); border: 1px solid #ef4444; border-radius: 8px; padding: 14px 18px; margin-bottom: 16px; color: #fca5a5;">
      <div style="display: flex; align-items: flex-start; gap: 12px;">
        <span style="font-size: 22px; line-height: 1;">⚠️</span>
        <div>
          <strong style="color: #f87171; font-size: 15px; letter-spacing: 0.5px;">DRIFT WARNING DETECTED</strong>
          <div id="drift-message" style="margin-top: 4px; color: #fecaca; font-size: 13px;"></div>
          <div id="drift-meta" style="margin-top: 6px; color: #fca5a5; font-size: 12px; font-family: var(--font-mono);"></div>
          <div style="margin-top: 6px; font-size: 12px; color: var(--text-muted);">
            Guardian is safely holding rollout execution to prevent unintended state changes. Verify in Flagsmith or trigger an operator Reset.
          </div>
        </div>
      </div>
    </div>

    <!-- Human Approval Banner -->
    <div id="awaiting-banner" class="banner-awaiting" style="display: none;">
      <div>
        <strong>Human Approval Required:</strong> All guardrails satisfied. Ready for final 100% promotion.
      </div>
      <button class="btn-approve" onclick="handleApprove()">Approve 100%</button>
    </div>

    <!-- Multi-Rollout Overview (if multiple rollouts active) -->
    <div id="multi-rollout-section" style="display: none; margin-bottom: 24px;">
      <div class="card">
        <div class="card-title">All Managed Rollouts</div>
        <div id="multi-rollout-container" style="display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; margin-top: 10px;">
          <!-- Dynamically populated per flag -->
        </div>
      </div>
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

    <!-- Flagsmith Experimentation Card -->
    <div id="experiment-card" class="card" style="margin-bottom: 24px; display: none;">
      <div class="card-title">
        <div style="display: flex; align-items: center; gap: 8px;">
          <span>🧪 Flagsmith Experimentation (Checkout A/B)</span>
          <span id="exp-status-pill" class="status-pill pill-healthy">HEALTHY</span>
        </div>
        <span id="exp-as-of" class="mono" style="font-size: 11px; color: var(--text-muted);">results computed by Flagsmith from events sent by the demo app, as of --</span>
      </div>
      <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-top: 12px;">
        <div style="background: rgba(255,255,255,0.03); padding: 14px; border-radius: 8px; border: 1px solid var(--card-border);">
          <div style="font-size: 11px; color: var(--text-muted); text-transform: uppercase;">Chance to be Worse</div>
          <div id="exp-chance-worse" class="mono" style="font-size: 22px; font-weight: 700; color: #34d399; margin: 4px 0;">--</div>
          <div style="font-size: 11px; color: var(--text-muted);">Threshold: &lt; 10.0%</div>
        </div>
        <div style="background: rgba(255,255,255,0.03); padding: 14px; border-radius: 8px; border: 1px solid var(--card-border);">
          <div style="font-size: 11px; color: var(--text-muted); text-transform: uppercase;">Sample Ratio Mismatch (SRM)</div>
          <div id="exp-srm" class="mono" style="font-size: 22px; font-weight: 700; color: #34d399; margin: 4px 0;">No SRM</div>
          <div id="exp-srm-p" class="mono" style="font-size: 11px; color: var(--text-muted);">p-value: -- (limit: 0.001)</div>
        </div>
        <div style="background: rgba(255,255,255,0.03); padding: 14px; border-radius: 8px; border: 1px solid var(--card-border);">
          <div style="font-size: 11px; color: var(--text-muted); text-transform: uppercase;">Relative Conversion Lift</div>
          <div id="exp-lift" class="mono" style="font-size: 22px; font-weight: 700; color: #818cf8; margin: 4px 0;">--</div>
          <div id="exp-samples" class="mono" style="font-size: 11px; color: var(--text-muted);">Total samples: --</div>
        </div>
      </div>
      <div id="exp-gate-note" style="margin-top: 14px; font-size: 12px; padding: 10px 14px; border-radius: 6px; background: rgba(16, 185, 129, 0.1); border: 1px solid rgba(16, 185, 129, 0.2); color: #34d399;">
        ✅ Experiment gate healthy: Bayesian confidence and traffic balance clear for promotion.
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
    let activeFlagName = '';

    async function fetchStatus() {
      try {
        const url = activeFlagName ? `/status?flag_name=${encodeURIComponent(activeFlagName)}` : '/status';
        const res = await fetch(url);
        if (!res.ok) return;
        const data = await res.json();
        renderDashboard(data);
      } catch (err) {
        console.error('Failed to fetch status:', err);
      }
    }

    function renderDashboard(data) {
      activeFlagName = data.flag_name;
      document.getElementById('state-display').innerText = data.state;
      document.getElementById('pct-display').innerText = data.percentage + '% Allocation';
      document.getElementById('flag-badge').innerText = 'Flagsmith Flag: ' + data.flag_name;

      // Drift Warning Banner
      const drift = data.drift;
      const driftBanner = document.getElementById('drift-banner');
      if (drift && drift.detected) {
        driftBanner.style.display = 'block';
        document.getElementById('drift-message').innerText = drift.message || 'Flagsmith actual flag percentage differs from stored state.';
        const changedByStr = drift.changed_by ? `Changed by: ${drift.changed_by}` : '';
        const sourceStr = drift.source ? `Source: ${drift.source}` : '';
        const diffStr = (drift.expected_percentage !== undefined && drift.actual_percentage !== undefined)
          ? `Expected: ${drift.expected_percentage}% | Received: ${drift.actual_percentage}%`
          : '';
        const metaParts = [changedByStr, sourceStr, diffStr].filter(Boolean);
        document.getElementById('drift-meta').innerText = metaParts.join(' • ');
        document.getElementById('state-display').innerText = 'DRIFT DETECTED';
        document.getElementById('state-display').style.color = '#ef4444';
      } else {
        driftBanner.style.display = 'none';
      }

      if (data.soak_time_seconds) {
        document.getElementById('soak-info').innerText = `Soak: ${data.soak_elapsed_seconds || 0}s / ${data.soak_time_seconds}s`;
      }

      // Multi-rollout cards
      const rollouts = data.rollouts || {};
      const flagKeys = Object.keys(rollouts);
      const multiSec = document.getElementById('multi-rollout-section');
      const multiContainer = document.getElementById('multi-rollout-container');

      if (flagKeys.length > 1) {
        multiSec.style.display = 'block';
        multiContainer.innerHTML = flagKeys.map(k => {
          const r = rollouts[k];
          const isSelected = r.flag_name === data.flag_name;
          const driftBadge = r.drift && r.drift.detected ? '<span class="status-pill pill-breached">DRIFT</span>' : '';
          return `
            <div style="background: rgba(255,255,255,0.03); border: 1px solid ${isSelected ? 'var(--accent-primary)' : 'var(--card-border)'}; border-radius: 8px; padding: 14px; cursor: pointer;" onclick="selectRollout('${r.flag_name}')">
              <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                <strong style="color: ${isSelected ? '#818cf8' : 'var(--text-main)'}; font-size: 14px;">${r.flag_name}</strong>
                ${driftBadge}
              </div>
              <div style="display: flex; gap: 8px; align-items: baseline;">
                <span style="font-size: 18px; font-weight: 700; color: ${r.state === 'PROMOTED' ? '#34d399' : (r.state === 'ROLLED_BACK' ? '#f87171' : '#f9fafb')}">${r.state}</span>
                <span class="mono" style="font-size: 13px; color: #818cf8;">${r.percentage}%</span>
              </div>
            </div>
          `;
        }).join('');
      } else {
        multiSec.style.display = 'none';
      }

      // Banner & Approve button
      const isAwaiting = data.state === 'AWAITING_APPROVAL';
      const isEligible = (data.is_eligible_for_approval !== undefined) ? Boolean(data.is_eligible_for_approval) : (data.experiment ? (data.experiment.is_eligible_for_approval === true) : true);
      document.getElementById('awaiting-banner').style.display = isAwaiting ? 'flex' : 'none';
      const approveBtn = document.getElementById('btn-approve-action');
      approveBtn.disabled = !isAwaiting || !isEligible;
      if (isAwaiting && !isEligible) {
        approveBtn.title = data.approval_block_reason || data.experiment?.block_reason || 'Approval blocked by experiment guardrail';
      } else {
        approveBtn.title = '';
      }

      // Color code state badge
      const stateEl = document.getElementById('state-display');
      if (drift && drift.detected) stateEl.style.color = '#ef4444';
      else if (data.state === 'PROMOTED') stateEl.style.color = '#34d399';
      else if (data.state === 'ROLLED_BACK') stateEl.style.color = '#f87171';
      else if (data.state === 'AWAITING_APPROVAL') stateEl.style.color = '#fbbf24';
      else stateEl.style.color = '#f9fafb';

      // Experiment Card
      if (data.experiment) {
        document.getElementById('experiment-card').style.display = 'block';
        const exp = data.experiment;
        const asOfStr = exp.as_of ? new Date(exp.as_of).toUTCString() : (exp.as_of || '--');
        const shortcutTag = exp.demo_shortcut ? ' (demo shortcut)' : '';
        document.getElementById('exp-as-of').innerText = 'results computed by Flagsmith from events sent by the demo app, as of ' + asOfStr + shortcutTag;

        const cwEl = document.getElementById('exp-chance-worse');
        if (exp.is_collecting_data || exp.chance_to_be_worse === null || exp.chance_to_be_worse === undefined) {
          cwEl.innerText = 'n/a (collecting data)';
          cwEl.style.color = '#fbbf24';
        } else {
          const cwVal = typeof exp.chance_to_be_worse === 'number' ? exp.chance_to_be_worse.toFixed(1) + '%' : exp.chance_to_be_worse;
          cwEl.innerText = cwVal;
          if (typeof exp.chance_to_be_worse === 'number' && exp.chance_to_be_worse > 10.0) {
            cwEl.style.color = '#f87171';
          } else {
            cwEl.style.color = '#34d399';
          }
        }

        const srmEl = document.getElementById('exp-srm');
        const srmPEl = document.getElementById('exp-srm-p');
        if (exp.srm_warning) {
          srmEl.innerText = 'SRM DETECTED!';
          srmEl.style.color = '#f87171';
          srmPEl.innerText = `p-value: ${exp.srm_p_value !== null && exp.srm_p_value !== undefined ? exp.srm_p_value.toFixed(6) : 'n/a (collecting data)'} (< 0.001)`;
        } else if (exp.is_collecting_data || exp.srm_p_value === null || exp.srm_p_value === undefined) {
          srmEl.innerText = 'Collecting data';
          srmEl.style.color = '#fbbf24';
          srmPEl.innerText = 'p-value: n/a (collecting data)';
        } else {
          srmEl.innerText = 'No SRM';
          srmEl.style.color = '#34d399';
          srmPEl.innerText = `p-value: ${exp.srm_p_value.toFixed(4)} (limit: 0.001)`;
        }

        const liftVal = exp.lift !== null && exp.lift !== undefined ? ((exp.lift >= 0 ? '+' : '') + (exp.lift * 100).toFixed(1) + '%') : '--';
        document.getElementById('exp-lift').innerText = liftVal;
        document.getElementById('exp-samples').innerText = `Total samples: ${(exp.total_samples || 0).toLocaleString()}`;

        const pillEl = document.getElementById('exp-status-pill');
        const gateNoteEl = document.getElementById('exp-gate-note');
        if (exp.is_collecting_data) {
          pillEl.className = 'status-pill pill-insufficient';
          pillEl.innerText = 'COLLECTING DATA';
          gateNoteEl.style.background = 'rgba(245, 158, 11, 0.1)';
          gateNoteEl.style.borderColor = 'rgba(245, 158, 11, 0.3)';
          gateNoteEl.style.color = '#fbbf24';
          gateNoteEl.innerHTML = '⏳ <strong>Flagsmith is still collecting data (under 50 identities per arm or under 5 conversions). Approval gate blocked.</strong>';
        } else if (!exp.is_eligible_for_approval && exp.block_reason) {
          pillEl.className = 'status-pill pill-breached';
          pillEl.innerText = 'GATE BLOCKED';
          gateNoteEl.style.background = 'rgba(239, 68, 68, 0.1)';
          gateNoteEl.style.borderColor = 'rgba(239, 68, 68, 0.3)';
          gateNoteEl.style.color = '#f87171';
          gateNoteEl.innerHTML = `⚠️ <strong>${exp.block_reason}</strong>`;
        } else {
          pillEl.className = 'status-pill pill-healthy';
          pillEl.innerText = 'HEALTHY';
          gateNoteEl.style.background = 'rgba(16, 185, 129, 0.1)';
          gateNoteEl.style.borderColor = 'rgba(16, 185, 129, 0.2)';
          gateNoteEl.style.color = '#34d399';
          gateNoteEl.innerHTML = '✅ Experiment gate healthy: Bayesian confidence and traffic balance clear for promotion.';
        }
      }

      // Stepper
      renderStepper(data);

      // Guardrails
      renderGuardrails(data.guardrails || []);

      // Timeline
      renderTimeline(data.timeline || data.recent_audit_entries || []);
    }

    function selectRollout(flagName) {
      activeFlagName = flagName;
      fetchStatus();
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

        const isExpGuardrail = g.metric_source === 'flagsmith_experiment' || g.name.startsWith('checkout_ab');
        const val = (g.value !== null && g.value !== undefined)
          ? (typeof g.value === 'number' ? g.value.toFixed(2) + '%' : g.value)
          : (isExpGuardrail ? 'n/a (collecting data)' : '--');
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

        let timeStr = '--';
        if (entry.timestamp) {
          const parsed = Date.parse(entry.timestamp);
          timeStr = isNaN(parsed) ? String(entry.timestamp) : new Date(parsed).toLocaleTimeString();
        }
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

    async function handleApprove() {
      try {
        const body = activeFlagName ? { flag_name: activeFlagName } : {};
        const res = await fetch('/approve', {
          method: 'POST',
          headers: getAuthHeaders(),
          body: JSON.stringify(body),
        });
        const json = await res.json();
        if (!res.ok) alert('Approval Failed: ' + (json.detail || JSON.stringify(json)));
        fetchStatus();
      } catch (err) {
        alert('Approval Request Error: ' + err);
      }
    }

    async function handleRollback() {
      if (!confirm(`Are you sure you want to rollback ${activeFlagName || 'the flag'} to 0%?`)) return;
      try {
        const body = { reason: 'Operator initiated rollback via status UI' };
        if (activeFlagName) body.flag_name = activeFlagName;
        const res = await fetch('/rollback', {
          method: 'POST',
          headers: getAuthHeaders(),
          body: JSON.stringify(body),
        });
        const json = await res.json();
        if (!res.ok) alert('Rollback Failed: ' + (json.detail || JSON.stringify(json)));
        fetchStatus();
      } catch (err) {
        alert('Rollback Request Error: ' + err);
      }
    }

    async function handleReset() {
      if (!confirm(`Are you sure you want to reset ${activeFlagName || 'the rollout'} back to PENDING?`)) return;
      try {
        const body = activeFlagName ? { flag_name: activeFlagName } : {};
        const res = await fetch('/reset', {
          method: 'POST',
          headers: getAuthHeaders(),
          body: JSON.stringify(body),
        });
        const json = await res.json();
        if (!res.ok) alert('Reset Failed: ' + (json.detail || JSON.stringify(json)));
        fetchStatus();
      } catch (err) {
        alert('Reset Request Error: ' + err);
      }
    }

    async function handleTick() {
      try {
        const url = activeFlagName ? `/tick?flag_name=${encodeURIComponent(activeFlagName)}` : '/tick';
        await fetch(url, { method: 'POST', headers: getAuthHeaders() });
        fetchStatus();
      } catch (err) {
        console.error('Tick Error:', err);
      }
    }

    // Auto-poll status every 2 seconds
    fetchStatus();
    setInterval(fetchStatus, 2000);
  </script>
</body>
</html>
"""
