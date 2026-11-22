#!/usr/bin/env python3
import os
import sys
import json
import time
import requests
from guardian.main import load_env_file
load_env_file()

API_URL = os.environ.get("FLAGSMITH_URL", "http://localhost:8000/api/v1")
ADMIN_KEY = os.environ.get("FLAGSMITH_ADMIN_API_KEY", "")
ENV_KEY = os.environ.get("FLAGSMITH_ENVIRONMENT_KEY", "")
PROJECT_ID = int(os.environ.get("FLAGSMITH_PROJECT_ID", "1"))
ENV_ID = int(os.environ.get("FLAGSMITH_ENVIRONMENT_ID", "1"))

HEADERS = {
    "Authorization": f"Api-Key {ADMIN_KEY}",
    "Content-Type": "application/json"
}

def log(section, msg):
    print(f"\n[{section}] {msg}")

def check_q1_and_q3():
    log("Q1 & Q3", "Testing flag modification during active experiment and experiment results endpoint...")

    # 1. Create a metric
    metric_res = requests.post(
        f"{API_URL}/environments/{ENV_KEY}/experiment-metrics/",
        headers=HEADERS,
        json={
            "name": f"Spike Metric {int(time.time())}",
            "aggregation": "count",
            "direction": "up",
            "definition": {"version": 1, "event": "purchase"}
        }
    )
    log("Q1/Q3", f"Create Metric: Status {metric_res.status_code}")
    metric = metric_res.json()
    metric_id = metric.get("id")

    # 2. Create a multivariate feature
    feat_name = f"spike_exp_flag_{int(time.time())}"
    feat_res = requests.post(
        f"{API_URL}/projects/{PROJECT_ID}/features/",
        headers=HEADERS,
        json={
            "name": feat_name,
            "type": "MULTIVARIATE",
            "initial_value": "control_val",
            "default_enabled": True,
            "multivariate_options": [
                {
                    "type": "unicode",
                    "string_value": "treatment_val",
                    "default_percentage_allocation": 50.0,
                    "key": "variation_a"
                }
            ]
        }
    )
    log("Q1/Q3", f"Create Feature: Status {feat_res.status_code}")
    feat = feat_res.json()
    feature_id = feat.get("id")
    mv_opt_id = feat["multivariate_options"][0]["id"]
    print(f"Feature created: {feat_name} (ID: {feature_id}, MV Option ID: {mv_opt_id})")

    # 3. Create experiment
    exp_res = requests.post(
        f"{API_URL}/environments/{ENV_KEY}/experiments/",
        headers=HEADERS,
        json={
            "feature": feature_id,
            "name": f"Spike Experiment {int(time.time())}",
            "hypothesis": "Testing flag modification while running",
            "metrics": [
                {
                    "metric": metric_id,
                    "expected_direction": "increase"
                }
            ],
            "experiment_rollout": {
                "enabled": True,
                "rollout_percentage": 100.0,
                "feature_state_value": {
                    "type": "string",
                    "value": "control_val"
                },
                "multivariate_feature_state_values": [
                    {
                        "multivariate_feature_option": mv_opt_id,
                        "percentage_allocation": 50.0
                    }
                ]
            }
        }
    )
    log("Q1/Q3", f"Create Experiment: Status {exp_res.status_code}")
    exp = exp_res.json()
    experiment_id = exp.get("id")
    print("Experiment created:", experiment_id, json.dumps(exp, indent=2))

    # 4. Start experiment
    start_res = requests.post(
        f"{API_URL}/environments/{ENV_KEY}/experiments/{experiment_id}/start/",
        headers=HEADERS
    )
    log("Q1", f"Start Experiment: Status {start_res.status_code}")
    started_exp = start_res.json()
    print("Started Experiment status:", started_exp.get("status"))

    # 5. NOW TEST Q1: Can we change or disable the flag via Admin API while experiment is RUNNING?
    # Get all feature states for this feature (including the experiment's rollout segment override)
    all_fs_res = requests.get(
        f"{API_URL}/features/featurestates/?environment={ENV_ID}&feature={feature_id}",
        headers=HEADERS
    )
    all_fs = all_fs_res.json().get("results", [])
    print(f"Found {len(all_fs)} feature states for feature {feature_id}:")
    for f in all_fs:
        print(f"  - FS ID: {f['id']}, segment: {f.get('feature_segment')}, enabled: {f.get('enabled')}")

    env_fs = next(f for f in all_fs if f.get("feature_segment") is None)
    segment_fs = next((f for f in all_fs if f.get("feature_segment") is not None), None)

    # Attempt 1: Disable environment feature state
    disable_res = requests.patch(
        f"{API_URL}/features/featurestates/{env_fs['id']}/",
        headers=HEADERS,
        json={"environment": ENV_ID, "enabled": False}
    )
    log("Q1", f"Attempt 1 - DISABLE environment feature state {env_fs['id']}: Status {disable_res.status_code}")
    print("Disable response body:", disable_res.status_code, disable_res.text)

    # Attempt 2: If there is a segment override from experiment rollout, try to disable it!
    if segment_fs:
        override_disable_res = requests.patch(
            f"{API_URL}/features/featurestates/{segment_fs['id']}/",
            headers=HEADERS,
            json={"environment": ENV_ID, "enabled": False}
        )
        log("Q1", f"Attempt 2 - DISABLE experiment rollout segment override {segment_fs['id']}: Status {override_disable_res.status_code}")
        print("Override disable response body:", override_disable_res.status_code, override_disable_res.text)

        # Attempt 2b: Try to change MV weights on segment override
        mv_val = segment_fs["multivariate_feature_state_values"][0]
        override_mv_res = requests.patch(
            f"{API_URL}/features/featurestates/{segment_fs['id']}/",
            headers=HEADERS,
            json={
                "environment": ENV_ID,
                "multivariate_feature_state_values": [
                    {
                        "id": mv_val["id"],
                        "multivariate_feature_option": mv_val["multivariate_feature_option"],
                        "percentage_allocation": 0.0
                    }
                ]
            }
        )
        log("Q1", f"Attempt 2b - CHANGE MV weights on experiment rollout segment override: Status {override_mv_res.status_code}")
        print("Override MV response body:", override_mv_res.status_code, override_mv_res.text)

    # Attempt 3: Try to update the experiment's rollout via the official rollout endpoint
    rollout_update_res = requests.post(
        f"{API_URL}/environments/{ENV_KEY}/experiments/{experiment_id}/rollout/",
        headers=HEADERS,
        json={
            "enabled": False,
            "rollout_percentage": 0.0,
            "feature_state_value": {"type": "string", "value": "control_val"},
            "multivariate_feature_state_values": [
                {
                    "multivariate_feature_option": mv_opt_id,
                    "percentage_allocation": 0.0
                }
            ]
        }
    )
    log("Q1", f"Attempt 3 - UPDATE experiment via experiment rollout endpoint: Status {rollout_update_res.status_code}")
    print("Rollout update response body:", rollout_update_res.status_code, rollout_update_res.text)

    # Attempt 4: Try to DELETE the feature while experiment is RUNNING
    del_feat_res = requests.delete(
        f"{API_URL}/projects/{PROJECT_ID}/features/{feature_id}/",
        headers=HEADERS
    )
    log("Q1", f"Attempt 4 - DELETE feature while experiment RUNNING: Status {del_feat_res.status_code}")
    print("Delete feature response body:", del_feat_res.status_code, del_feat_res.text)

    # 6. NOW TEST Q3: Experiment results endpoint and refresh throttling
    results_res = requests.get(
        f"{API_URL}/environments/{ENV_KEY}/experiments/{experiment_id}/results/",
        headers=HEADERS
    )
    log("Q3", f"Get Experiment Results: Status {results_res.status_code}")
    print("Results response body:", json.dumps(results_res.json(), indent=2))

    # Test refresh results endpoint
    refresh1 = requests.post(
        f"{API_URL}/environments/{ENV_KEY}/experiments/{experiment_id}/results/refresh/",
        headers=HEADERS
    )
    log("Q3", f"First Refresh Results: Status {refresh1.status_code}")
    print("Refresh 1 text:", refresh1.status_code, refresh1.text)

    refresh2 = requests.post(
        f"{API_URL}/environments/{ENV_KEY}/experiments/{experiment_id}/results/refresh/",
        headers=HEADERS
    )
    log("Q3", f"Second Immediate Refresh Results (throttling check): Status {refresh2.status_code}")
    print("Refresh 2 text:", refresh2.status_code, refresh2.text)

if __name__ == "__main__":
    check_q1_and_q3()
