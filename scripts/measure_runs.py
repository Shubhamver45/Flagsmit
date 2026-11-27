#!/usr/bin/env python3
"""
scripts/measure_runs.py - Extract performance metrics from demo-run-*.log files
and output summary statistics table for README.md.
"""

import os
import re
import statistics
import sys
from typing import List, Tuple

def parse_run_logs(pattern: str = "docs/runs/demo-run-{i}.log", num_runs: int = 5) -> Tuple[List[str], List[float], List[float]]:
    files_read = []
    drift_times: List[float] = []
    rollback_times: List[float] = []

    for i in range(1, num_runs + 1):
        fname = pattern.format(i=i)
        if not os.path.exists(fname):
            print(f"Error: Log file '{fname}' not found.", file=sys.stderr)
            sys.exit(1)
        files_read.append(fname)
        with open(fname, "r", encoding="utf-8") as f:
            content = f.read()

        # Match drift latency: "DRIFT WARNING DETECTED (measured latency: 7.74s)"
        m_drift = re.search(r"measured latency:\s*([0-9.]+)s", content)
        if m_drift:
            drift_times.append(float(m_drift.group(1)))
        else:
            print(f"Error: Drift latency pattern not matched in {fname}", file=sys.stderr)
            sys.exit(1)

        # Match rollback time: "Time to Rollback: 5.4s" or "completed in 5.4s"
        m_rb = re.search(r"Time to Rollback:\s*([0-9.]+)s", content)
        if not m_rb:
            m_rb = re.search(r"rollback to 0% completed in\s*([0-9.]+)s", content)
        if m_rb:
            rollback_times.append(float(m_rb.group(1)))
        else:
            print(f"Error: Rollback time pattern not matched in {fname}", file=sys.stderr)
            sys.exit(1)

    return files_read, drift_times, rollback_times

def main():
    files, drift_times, rollback_times = parse_run_logs()

    print("Log files evaluated:")
    for f in files:
        print(f"  • {f}")
    print()

    # Format Markdown Table
    d_min, d_med, d_max = min(drift_times), statistics.median(drift_times), max(drift_times)
    r_min, r_med, r_max = min(rollback_times), statistics.median(rollback_times), max(rollback_times)

    header = "| Metric | Run 1 | Run 2 | Run 3 | Run 4 | Run 5 | Min | Median | Max | Proved In |"
    sep = "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |"
    row_drift = f"| **Drift Detection Latency** | {drift_times[0]:.2f}s | {drift_times[1]:.2f}s | {drift_times[2]:.2f}s | {drift_times[3]:.2f}s | {drift_times[4]:.2f}s | **{d_min:.2f}s** | **{d_med:.2f}s** | **{d_max:.2f}s** | `demo-run-*.log`, `tests/test_webhooks_drift.py` |"
    row_rollback = f"| **Automated Rollback Time** | {rollback_times[0]:.2f}s | {rollback_times[1]:.2f}s | {rollback_times[2]:.2f}s | {rollback_times[3]:.2f}s | {rollback_times[4]:.2f}s | **{r_min:.2f}s** | **{r_med:.2f}s** | **{r_max:.2f}s** | `demo-run-*.log`, `tests/test_guardian_engine.py` |"

    print(header)
    print(sep)
    print(row_drift)
    print(row_rollback)

if __name__ == "__main__":
    main()
