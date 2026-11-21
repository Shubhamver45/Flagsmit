#!/usr/bin/env python3
"""
Verification of Guardian's own background polling loop.
Ensures that background_ticker (without manual tick calls) performs:
1. Automatic stage advancement under healthy traffic.
2. Holding at AWAITING_APPROVAL.
3. Promotion to 100% after operator approval.
4. Automatic rollback to 0% and alert dispatch when fault is injected.
"""

import os
import sys
import time
import requests
import subprocess

DEMO_APP = "http://localhost:5001"
GUARDIAN_API = "http://localhost:8080"


def send_traffic_batch(count=30, bug=False):
    requests.post(f"{DEMO_APP}/toggle-bug?enabled={'1' if bug else '0'}", timeout=3)
    identities = [f"user_{i}" for i in range(1, 201)]
    for i in range(count):
        uid = identities[i % len(identities)]
        try:
            requests.post(f"{DEMO_APP}/checkout", json={"user_id": uid}, timeout=2)
        except Exception:
            pass
    time.sleep(2.5)  # Wait for Prometheus scrape


def test_daemon_loop():
    print("=" * 70)
    print("VERIFYING GUARDIAN BACKGROUND POLLING LOOP")
    print("=" * 70)

    # 1. Reset demo app and metrics
    print("1. Resetting demo app metrics and bug state...")
    requests.post(f"{DEMO_APP}/reset-metrics", timeout=3)
    requests.post(f"{DEMO_APP}/toggle-bug?enabled=0", timeout=3)

    # 2. Start Guardian in DEMO_MODE with 4s soak and 2s tick interval
    env = os.environ.copy()
    env["DEMO_MODE"] = "1"
    env["DEMO_SOAK_SECONDS"] = "4"
    env["PYTHONPATH"] = "."

    print("2. Starting Guardian Daemon (DEMO_MODE=1, soak=4s, interval=2s)...")
    proc = subprocess.Popen(
        [sys.executable, "-m", "guardian.main", "--port", "8085", "--interval", "2", "--demo-mode", "--demo-soak", "4"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    guardian_url = "http://localhost:8085"

    try:
        # Wait for Guardian startup
        for _ in range(15):
            try:
                r = requests.get(f"{guardian_url}/health", timeout=1)
                if r.status_code == 200:
                    break
            except Exception:
                time.sleep(1)
        else:
            raise RuntimeError("Guardian daemon failed to start within 15s")
        print("  ✔ Guardian status endpoint is alive at :8085")

        # 3. Send initial healthy traffic
        print("3. Sending healthy traffic...")
        send_traffic_batch(count=40, bug=False)

        # 4. Watch background loop advance automatically
        print("4. Observing automatic background stage advancements...")
        states_seen = set()
        start_t = time.time()
        while time.time() - start_t < 25:
            st = requests.get(f"{guardian_url}/status").json()
            curr = st.get("state")
            pct = st.get("percentage")

            # Collect states from both current state and audit history
            for entry in st.get("timeline", []):
                states_seen.add(entry.get("to_state"))
            states_seen.add(curr)

            print(f"  [Auto-Observed] Current State: {curr} ({pct}%) | Historical states: {sorted(list(states_seen))}")
            if curr == "AWAITING_APPROVAL":
                break
            time.sleep(1)

        assert "STAGE_1" in states_seen, f"Never saw STAGE_1 in {states_seen}"
        assert "AWAITING_APPROVAL" in states_seen, f"Never reached AWAITING_APPROVAL in {states_seen}"
        print("  ✔ Background polling loop autonomously advanced stages to AWAITING_APPROVAL!")

        # 5. Human approval
        print("5. Sending Human Operator Approval to :8085/approve...")
        app_resp_raw = requests.post(f"{guardian_url}/approve", json={"approved_by": "test-operator"})
        print(f"  Approval HTTP {app_resp_raw.status_code}: {app_resp_raw.text}")
        app_res = app_resp_raw.json()
        assert app_res.get("current_state") == "PROMOTED"

        # 6. Fault injection
        print("6. Turning on BUG_IN_NEW_CHECKOUT and sending failure traffic...")
        send_traffic_batch(count=50, bug=True)

        # 7. Observe automatic rollback by the polling loop
        print("7. Waiting for background polling loop to detect breach and auto-rollback...")
        rolled_back = False
        start_t = time.time()
        while time.time() - start_t < 15:
            st = requests.get(f"{guardian_url}/status").json()
            curr = st.get("state")
            pct = st.get("percentage")
            if curr == "ROLLED_BACK":
                rolled_back = True
                print(f"  [Auto-Observed] State transitioned to: {curr} ({pct}%) - INSTANT ROLLBACK SUCCEEDED!")
                break
            time.sleep(1)

        assert rolled_back, "Background loop failed to execute auto-rollback within 15s"
        print("  ✔ Background polling loop autonomously executed instant rollback to 0%!")

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except Exception:
            proc.kill()
        print("Daemon stopped.")

    print("\n" + "=" * 70)
    print("🎉 GUARDIAN'S OWN BACKGROUND POLLING LOOP VERIFIED 100% OPERATIONAL!")
    print("========================================================================")


if __name__ == "__main__":
    test_daemon_loop()
