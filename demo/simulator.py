#!/usr/bin/env python3
"""
Traffic Simulator for Demo Checkout App.
Sends requests from a few hundred distinct user identities and reports error rates per variant.
"""

import os
import sys
import time
import random
import requests

TARGET_URL = os.environ.get("DEMO_APP_URL", "http://localhost:5001/checkout")
PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")

NUM_IDENTITIES = int(os.environ.get("NUM_IDENTITIES", "200"))
TOTAL_REQUESTS = int(os.environ.get("TOTAL_REQUESTS", "500"))

def run_simulation():
    print(f"=== Starting Traffic Simulation ===")
    print(f"Target: {TARGET_URL}")
    print(f"Unique Identities: {NUM_IDENTITIES}")
    print(f"Total Requests: {TOTAL_REQUESTS}")
    print("-----------------------------------")

    identities = [f"user_{i}" for i in range(1, NUM_IDENTITIES + 1)]
    
    stats = {
        "old_flow": {"requests": 0, "errors": 0},
        "new_flow": {"requests": 0, "errors": 0},
    }

    start_time = time.time()
    for idx in range(1, TOTAL_REQUESTS + 1):
        user_id = random.choice(identities)
        try:
            resp = requests.post(TARGET_URL, json={"user_id": user_id}, timeout=5)
            data = resp.json()
            variant = data.get("variant", "unknown")
            is_error = resp.status_code >= 400
        except Exception as e:
            variant = "old_flow"
            is_error = True

        if variant not in stats:
            stats[variant] = {"requests": 0, "errors": 0}

        stats[variant]["requests"] += 1
        if is_error:
            stats[variant]["errors"] += 1

        if idx % 100 == 0 or idx == TOTAL_REQUESTS:
            print(f"Progress: {idx}/{TOTAL_REQUESTS} requests sent...")

    elapsed = time.time() - start_time
    print(f"\nSimulation completed in {elapsed:.2f}s.")
    print("\n=== Local Client-Side Results ===")
    for variant, val in stats.items():
        reqs = val["requests"]
        errs = val["errors"]
        rate = (errs / reqs * 100) if reqs > 0 else 0.0
        print(f"Variant: {variant:10s} | Requests: {reqs:4d} | Errors: {errs:3d} | Error Rate: {rate:6.2f}%")

    print("\nWaiting 4s for Prometheus to scrape demo-app metrics...")
    time.sleep(4)

    query_prometheus()

def query_prometheus():
    print("\n=== Prometheus Scraped Metrics ===")
    try:
        req_res = requests.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            params={"query": "sum by (variant) (checkout_requests_total)"},
            timeout=5
        ).json()

        err_res = requests.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            params={"query": "sum by (variant) (checkout_errors_total)"},
            timeout=5
        ).json()

        req_map = {}
        for r in req_res.get("data", {}).get("result", []):
            variant = r["metric"].get("variant", "unknown")
            req_map[variant] = float(r["value"][1])

        err_map = {}
        for r in err_res.get("data", {}).get("result", []):
            variant = r["metric"].get("variant", "unknown")
            err_map[variant] = float(r["value"][1])

        all_variants = sorted(set(list(req_map.keys()) + list(err_map.keys())))
        for variant in all_variants:
            total_reqs = req_map.get(variant, 0.0)
            total_errs = err_map.get(variant, 0.0)
            err_rate = (total_errs / total_reqs * 100.0) if total_reqs > 0 else 0.0
            print(f"Variant: {variant:10s} | Scraped Reqs: {int(total_reqs):4d} | Scraped Errs: {int(total_errs):3d} | Error Rate: {err_rate:6.2f}%")

    except Exception as e:
        print(f"Could not query Prometheus at {PROMETHEUS_URL}: {e}")

if __name__ == "__main__":
    run_simulation()
