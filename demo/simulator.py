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
IDENTITY_PREFIX = os.environ.get("IDENTITY_PREFIX", "user")

def run_simulation(prefix: str = None):
    pfx = prefix or IDENTITY_PREFIX
    print(f"=== Starting Traffic Simulation ===")
    print(f"Target: {TARGET_URL}")
    print(f"Unique Identities: {NUM_IDENTITIES} (prefix: {pfx})")
    print(f"Total Requests: {TOTAL_REQUESTS}")
    print("-----------------------------------")

    identities = [f"{pfx}_{i}" for i in range(1, NUM_IDENTITIES + 1)]
    
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

def run_continuous(delay: float = 0.5, batch_size: int = 5):
    print(f"=== Starting Continuous Traffic Simulation ===")
    print(f"Target: {TARGET_URL}")
    print(f"Unique Identities: {NUM_IDENTITIES}")
    print(f"Streaming {batch_size} requests every {delay}s across distinct identities...")
    identities = [f"user_{i}" for i in range(1, NUM_IDENTITIES + 1)]
    count = 0
    while True:
        for _ in range(batch_size):
            user_id = random.choice(identities)
            try:
                requests.post(TARGET_URL, json={"user_id": user_id}, timeout=3)
                count += 1
            except Exception:
                pass
        if count % 50 == 0:
            print(f"Sent {count} simulated checkout requests...")
        time.sleep(delay)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Demo Traffic Simulator")
    parser.add_argument("--continuous", action="store_true", help="Run continuous streaming traffic")
    parser.add_argument("--requests", type=int, default=TOTAL_REQUESTS, help="Total requests for batch simulation")
    parser.add_argument("--identities", type=int, default=NUM_IDENTITIES, help="Number of unique user identities")
    parser.add_argument("--prefix", type=str, default=IDENTITY_PREFIX, help="Prefix for user identities")
    parser.add_argument("--delay", type=float, default=0.5, help="Delay between batches in seconds")
    parser.add_argument("--batch", type=int, default=5, help="Batch size per streaming tick")
    args, _ = parser.parse_known_args()

    TOTAL_REQUESTS = args.requests
    NUM_IDENTITIES = args.identities

    if args.continuous or os.environ.get("CONTINUOUS") == "1":
        run_continuous(delay=args.delay, batch_size=args.batch)
    else:
        run_simulation(prefix=args.prefix)
