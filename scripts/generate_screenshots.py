#!/usr/bin/env python3
"""
scripts/generate_screenshots.py - Captures dashboard screenshots for:
1. healthy_rollout.png
2. drift_banner.png
3. approval_blocked.png
4. rollback.png
"""

import os
import subprocess
import time
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import threading

from guardian.status_page import STATUS_PAGE_HTML

app = FastAPI()
current_status = {}

@app.get("/", response_class=HTMLResponse)
def index():
    return STATUS_PAGE_HTML

@app.get("/status")
def get_status():
    return current_status

def run_server():
    uvicorn.run(app, host="127.0.0.1", port=8099, log_level="warning")

def _resolve_chrome_binary() -> str:
    import shutil
    candidates = [
        os.environ.get("CHROME_BIN"),
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for path in candidates:
        if path and os.path.exists(path):
            return path
    raise RuntimeError(
        "No Chromium/Chrome binary found. Set CHROME_BIN or install google-chrome / chromium."
    )


def capture_screenshot(url: str, output_path: str):
    chrome_bin = _resolve_chrome_binary()
    cmd = [
        chrome_bin,
        "--headless",
        "--hide-scrollbars",
        f"--screenshot={output_path}",
        "--window-size=1280,950",
        "--virtual-time-budget=2500",
        url,
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def main():
    os.makedirs("docs/img", exist_ok=True)
    t = threading.Thread(target=run_server, daemon=True)
    t.start()
    time.sleep(1.5)

    global current_status

    # 1. Healthy Rollout (STAGE_2 at 5.0%)
    current_status = {
        "flag_name": "new_checkout",
        "state": "STAGE_2",
        "stage": "STAGE_2",
        "percentage": 5.0,
        "stage_index": 1,
        "is_approved": False,
        "is_eligible_for_approval": True,
        "approval_block_reason": None,
        "stages": [1.0, 5.0, 25.0, 100.0],
        "soak_time_seconds": 300,
        "soak_elapsed_seconds": 142.5,
        "drift": {"detected": False},
        "guardrails": [
            {
                "name": "checkout_error_rate",
                "threshold": 5.0,
                "comparison": "gt",
                "metric_source": "prometheus",
                "status": "HEALTHY",
                "value": 0.42,
                "sample_size": 240,
                "is_breached": False,
                "reason": "Metric value 0.42% <= threshold 5.0%",
            },
            {
                "name": "checkout_p99_latency",
                "threshold": 500.0,
                "comparison": "gt",
                "metric_source": "prometheus",
                "status": "HEALTHY",
                "value": 118.5,
                "sample_size": 240,
                "is_breached": False,
                "reason": "Metric value 118.5ms <= threshold 500.0ms",
            }
        ],
        "experiment": {
            "name": "Checkout A/B Experiment",
            "as_of": "2026-11-25T12:00:00Z",
            "is_collecting_data": False,
            "chance_to_be_worse": 4.2,
            "chance_to_win": 95.8,
            "lift": 12.4,
            "ci_low": 2.1,
            "ci_high": 22.8,
            "total_samples": 480,
            "control_n": 240,
            "variation_n": 240,
            "control_conversions": 156,
            "variation_conversions": 192,
            "srm_p_value": 0.852,
            "srm_warning": False,
            "status": "HEALTHY",
            "is_eligible_for_approval": True,
            "block_reason": None,
            "demo_shortcut": True,
        },
        "audit_log": [
            {
                "timestamp": "2026-11-25T12:00:00Z",
                "flag_name": "new_checkout",
                "from_state": "STAGE_1",
                "to_state": "STAGE_2",
                "decision": "ADVANCE",
                "reason": "Soak time satisfied (300s) and guardrails healthy. Advanced to STAGE_2 (5%).",
                "numbers": {"current_percentage": 5.0},
            },
            {
                "timestamp": "2026-11-25T11:55:00Z",
                "flag_name": "new_checkout",
                "from_state": "PENDING",
                "to_state": "STAGE_1",
                "decision": "ADVANCE",
                "reason": "Rollout initiated at Stage 1 (1%).",
                "numbers": {"current_percentage": 1.0},
            }
        ]
    }
    capture_screenshot("http://127.0.0.1:8099/", "docs/img/healthy_rollout.png")
    print("✔ Captured docs/img/healthy_rollout.png")

    # 2. Drift Warning Banner (STAGE_1 modified out-of-band to 50%)
    current_status = {
        "flag_name": "new_checkout",
        "state": "STAGE_1",
        "stage": "STAGE_1",
        "percentage": 50.0,
        "stage_index": 0,
        "is_approved": False,
        "is_eligible_for_approval": False,
        "approval_block_reason": "Rollout held due to active drift",
        "stages": [1.0, 5.0, 25.0, 100.0],
        "soak_time_seconds": 300,
        "soak_elapsed_seconds": 12.0,
        "drift": {
            "detected": True,
            "expected_percentage": 1.0,
            "actual_percentage": 50.0,
            "expected_state": "STAGE_1",
            "changed_by": "ops-engineer",
            "source": "webhook",
            "detected_at": "2026-11-25T12:01:24Z",
            "message": "Flag 'new_checkout' modified out-of-band by ops-engineer: actual 50.0% differs from expected 1.0% (STAGE_1)."
        },
        "guardrails": [
            {
                "name": "checkout_error_rate",
                "threshold": 5.0,
                "comparison": "gt",
                "metric_source": "prometheus",
                "status": "HEALTHY",
                "value": 0.38,
                "sample_size": 45,
                "is_breached": False,
                "reason": "Metric healthy",
            }
        ],
        "experiment": None,
        "audit_log": [
            {
                "timestamp": "2026-11-25T12:01:24Z",
                "flag_name": "new_checkout",
                "from_state": "STAGE_1",
                "to_state": "STAGE_1",
                "decision": "DRIFT",
                "reason": "[DRIFT DETECTED via Webhook] Flag 'new_checkout' modified out-of-band by ops-engineer: actual 50.0% differs from expected 1.0% (STAGE_1).",
                "numbers": {"drift": True, "actual": 50.0, "expected": 1.0},
            }
        ]
    }
    capture_screenshot("http://127.0.0.1:8099/", "docs/img/drift_banner.png")
    print("✔ Captured docs/img/drift_banner.png")

    # 3. Approval Blocked (AWAITING_APPROVAL with SRM Warning)
    current_status = {
        "flag_name": "new_checkout",
        "state": "AWAITING_APPROVAL",
        "stage": "STAGE_3",
        "percentage": 25.0,
        "stage_index": 2,
        "is_approved": False,
        "is_eligible_for_approval": False,
        "approval_block_reason": "Approval blocked: Sample Ratio Mismatch (SRM) detected (p-value 0.000000 < 0.001). Traffic assignment is corrupted.",
        "stages": [1.0, 5.0, 25.0, 100.0],
        "soak_time_seconds": 300,
        "soak_elapsed_seconds": 300.0,
        "drift": {"detected": False},
        "guardrails": [
            {
                "name": "checkout_error_rate",
                "threshold": 5.0,
                "comparison": "gt",
                "metric_source": "prometheus",
                "status": "HEALTHY",
                "value": 0.45,
                "sample_size": 650,
                "is_breached": False,
                "reason": "Metric healthy",
            }
        ],
        "experiment": {
            "name": "Checkout A/B Experiment",
            "as_of": "2026-11-25T12:04:30Z",
            "is_collecting_data": False,
            "chance_to_be_worse": None,
            "chance_to_win": None,
            "lift": None,
            "ci_low": None,
            "ci_high": None,
            "total_samples": 693,
            "control_n": 416,
            "variation_n": 277,
            "control_conversions": 139,
            "variation_conversions": 166,
            "srm_p_value": 0.00000013,
            "srm_warning": True,
            "status": "CORRUPT",
            "is_eligible_for_approval": False,
            "block_reason": "Approval blocked: Sample Ratio Mismatch (SRM) detected (p-value 0.000000 < 0.001). Traffic assignment is corrupted.",
            "demo_shortcut": True,
        },
        "audit_log": [
            {
                "timestamp": "2026-11-25T12:04:30Z",
                "flag_name": "new_checkout",
                "from_state": "STAGE_3",
                "to_state": "AWAITING_APPROVAL",
                "decision": "AWAIT_APPROVAL",
                "reason": "Soak time completed for STAGE_3 (25.0%). Ready for final stage (100%), but requires human approval.",
                "numbers": {"current_percentage": 25.0},
            }
        ]
    }
    capture_screenshot("http://127.0.0.1:8099/", "docs/img/approval_blocked.png")
    print("✔ Captured docs/img/approval_blocked.png")

    # 4. Rollback (ROLLED_BACK to 0.0% due to failure spike)
    current_status = {
        "flag_name": "new_checkout",
        "state": "ROLLED_BACK",
        "stage": "ROLLED_BACK",
        "percentage": 0.0,
        "stage_index": -1,
        "is_approved": False,
        "is_eligible_for_approval": False,
        "approval_block_reason": "Rollout is in terminal state ROLLED_BACK",
        "stages": [1.0, 5.0, 25.0, 100.0],
        "soak_time_seconds": 300,
        "soak_elapsed_seconds": 0.0,
        "drift": {"detected": False},
        "guardrails": [
            {
                "name": "checkout_error_rate",
                "threshold": 5.0,
                "comparison": "gt",
                "metric_source": "prometheus",
                "status": "BREACH",
                "value": 6.51,
                "sample_size": 320,
                "is_breached": True,
                "reason": "Metric value 6.51% > threshold 5.0% (BREACH). Instant rollback executed.",
            }
        ],
        "experiment": None,
        "audit_log": [
            {
                "timestamp": "2026-11-25T12:03:42Z",
                "flag_name": "new_checkout",
                "from_state": "PROMOTED",
                "to_state": "ROLLED_BACK",
                "decision": "ROLLBACK",
                "reason": "Metric value 6.51% > threshold 5.0% (BREACH). Instant automated rollback to 0.0% executed.",
                "numbers": {"current_percentage": 0.0, "breached_metric": "checkout_error_rate", "value": 6.51},
            }
        ]
    }
    capture_screenshot("http://127.0.0.1:8099/", "docs/img/rollback.png")
    print("✔ Captured docs/img/rollback.png")

if __name__ == "__main__":
    main()
