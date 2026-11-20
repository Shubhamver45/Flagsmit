# Feasibility Spike Report: Flagsmith Guarded Rollouts & Experimentation

**Date**: 2026-11-20  
**Environment**: Local Self-Hosted Flagsmith (API v2.293.0, PostgreSQL 15, ClickHouse 24.x)  
**Author**: Guardian Engine Feasibility Spike

---

## Executive Summary & Architectural Rule

> **CRITICAL ARCHITECTURAL FINDING (QUESTION 1 RESULT: LOCKED)**  
> When an experiment is created and running on a feature flag in Flagsmith, Flagsmith creates a protected **system segment override** (`rollout_segment`). Attempting to modify, disable, or change percentage allocations on this override via the standard FeatureState API returns **`HTTP 409 Conflict: {"code": "system_segment_modification", "message": "System segments and their overrides can't be changed directly. Change this one through experiment instead."}`**.  
>  
> Furthermore, updating the default environment feature state does not affect traffic because the system segment override takes precedence.  
>  
> **RULE FOR GUARDIAN**: **Rollout flags and experiment flags must stay separate.**  
> Guardian's progressive rollouts (phased percentage stepping, metric guardrail monitoring, auto-rollback to 0%) must operate directly on **normal feature flags** (via multivariate weight adjustments or standard user segment overrides), completely decoupled from Flagsmith's Experimentation engine flags. This rule must be carried into every subsequent design and implementation prompt.

---

## Detailed Investigation & Verified API Evidence

### 1. Flag Modification During an Active Experiment

#### Real Call Flow Tested:
1. Created metric via `POST /api/v1/environments/{env_key}/experiment-metrics/`.
2. Created multivariate feature `spike_exp_flag` via `POST /api/v1/projects/{proj_id}/features/`.
3. Created experiment via `POST /api/v1/environments/{env_key}/experiments/` with `experiment_rollout` specifying 50% treatment allocation.
4. Started experiment via `POST /api/v1/environments/{env_key}/experiments/{id}/start/` (`status` transitioned to `running`).
5. Attempted to mutate the feature states via Admin API (`Authorization: Api-Key <master_key>`).

#### Observations:
* **Feature States Created by Flagsmith**:
  Flagsmith maintains two feature states for the flag:
  1. Base Environment Feature State (`feature_segment = null`): Allocations are automatically reset to control (0% treatment).
  2. Experiment Segment Override Feature State (`feature_segment = <system_segment_id>`): Carries the active experiment rollout (50% treatment).
* **Attempting to modify the Experiment Rollout Override (`PATCH /api/v1/features/featurestates/{segment_fs_id}/`)**:
  * **Status**: `409 Conflict`
  * **Payload Received**:
    ```json
    {
      "code": "system_segment_modification",
      "message": "System segments and their overrides can't be changed directly. Change this one through experiment instead."
    }
    ```
* **Attempting to modify the Base Feature State (`PATCH /api/v1/features/featurestates/{base_fs_id}/`)**:
  * Returns `200 OK`, but has **zero impact** on evaluating traffic because the experiment segment override evaluates first.
* **Specialized Rollout Endpoint**:
  * The experiment's rollout can only be altered through `PATCH /api/v1/environments/{env_key}/experiments/{id}/rollout/`.
* **Conclusion**:
  **LOCKED**. Flagsmith's Experimentation engine locks the flag's effective rollout into system-managed segment overrides that reject direct operational modifications.

---

### 2. Percentage Rollouts on Normal Flags (Stepping & Rollback to 0)

Flagsmith provides two methods for percentage rollouts on normal flags:

#### Approach A: Multivariate Weight Stepping (Recommended for Guardian)
* A feature flag configured with type `MULTIVARIATE`, where the control is the default value and variations hold treatment values.
* **Endpoint**:
  `PATCH /api/v1/features/featurestates/{feature_state_id}/`
* **Step up to 25% Rollout**:
  ```http
  PATCH /api/v1/features/featurestates/2/ HTTP/1.1
  Authorization: Api-Key <master_key>
  Content-Type: application/json

  {
    "environment": 1,
    "multivariate_feature_state_values": [
      {
        "id": 2,
        "multivariate_feature_option": 2,
        "percentage_allocation": 25.0
      }
    ]
  }
  ```
  *Response*: `200 OK` with `percentage_allocation: 25.0`.
* **Step up to 50% Rollout**:
  Same endpoint with `"percentage_allocation": 50.0`.
* **Instant Rollback to 0%**:
  ```http
  PATCH /api/v1/features/featurestates/2/ HTTP/1.1
  Authorization: Api-Key <master_key>
  Content-Type: application/json

  {
    "environment": 1,
    "multivariate_feature_state_values": [
      {
        "id": 2,
        "multivariate_feature_option": 2,
        "percentage_allocation": 0.0
      }
    ]
  }
  ```
  *Alternative instant killswitch*: Pass `{"environment": 1, "enabled": false}` to disable the flag entirely.

#### Approach B: Standard Segment Override with Percentage Split
* Create a segment with rule condition `operator: "PERCENTAGE_SPLIT"`, `value: "25"`.
* Attach to feature via `POST /api/v1/features/feature-segments/`.
* Step up by updating the segment condition value (`"25"` -> `"50"` -> `"100"`).
* Rollback by setting value to `"0"`, setting `enabled: false` on the segment override feature state, or deleting the `feature-segment`.

---

### 3. Experiment Results Endpoint & Refresh Throttling

#### Results Endpoint:
* **URL**: `GET /api/v1/environments/{env_key}/experiments/{experiment_id}/results/`
* **Response Structure** (from `ExperimentResultsSerializer` and `ResultsSummary`):
  ```json
  {
    "results": {
      "as_of": "2026-11-20T11:28:45.123456Z",
      "is_final": false,
      "refresh_requested_at": "2026-11-20T11:28:45.123456Z",
      "last_error_at": null,
      "payload": {
        "srm_p_value": 0.4821,
        "metrics": [
          {
            "metric_id": 1,
            "variants": {
              "control": {"n": 1200, "sum": 120.0, "sum_squares": 120.0, "mean": 0.10, "variance": 0.09},
              "variation_a": {"n": 1195, "sum": 145.0, "sum_squares": 145.0, "mean": 0.1213, "variance": 0.1066}
            },
            "inference": {
              "variation_a": {
                "lift": 0.213,
                "ci_low": 0.021,
                "ci_high": 0.441,
                "chance_to_win": 0.968
              }
            },
            "conversions_timeseries": [...]
          }
        ],
        "exposures_timeseries": [...]
      }
    }
  }
  ```
* **Payload Fields Explained**:
  * `chance_to_win`: Bayesian posterior probability ($0.0 \le P \le 1.0$) that the treatment outperforms control.
  * `lift`: Relative percentage change vs. control ($\frac{\mu_T - \mu_C}{\mu_C}$).
  * `ci_low` / `ci_high`: 95% Credible Interval for relative lift using Delta method variance approximation.
  * `srm_p_value`: Chi-squared survival $p$-value checking Sample Ratio Mismatch against expected traffic allocation. If $p < 0.001$, traffic assignment is corrupt.

#### Results Refresh Endpoint & Throttling:
* **Trigger Refresh**: `POST /api/v1/environments/{env_key}/experiments/{experiment_id}/results/refresh/`
  * Returns `202 Accepted` on valid trigger.
* **Throttling Verification**:
  * When triggered again immediately:
    * **Status**: `429 Too Many Requests`
    * **Response**: `{"detail": "A refresh was requested recently. Try again later. Expected available in 300 seconds."}`
  * **Refresh Interval**: Strictly enforced at **300 seconds (5 minutes)** (`RESULTS_REFRESH_MIN_INTERVAL = timedelta(minutes=5)`).

---

### 4. Webhook Events Fired on Flag Changes

Flagsmith webhooks fire at both the **Environment** and **Organisation** level.

#### Event Types (`WebhookEventType` Enum):
1. **`FLAG_UPDATED`**:
   Fired whenever a flag's enabled state, default value, or multivariate allocations are updated.
   *Payload Structure*:
   ```json
   {
     "event_type": "FLAG_UPDATED",
     "data": {
       "new_state": {
         "id": 2,
         "feature": {"id": 2, "name": "rollout_flag", "type": "MULTIVARIATE"},
         "enabled": true,
         "feature_state_value": "off",
         "multivariate_feature_state_values": [
           {
             "id": 2,
             "multivariate_feature_option": 2,
             "percentage_allocation": 25.0
           }
         ],
         "environment": 1,
         "identity": null,
         "feature_segment": null
       },
       "previous_state": {
         "enabled": true,
         "feature_state_value": "off",
         "multivariate_feature_state_values": [
           {
             "id": 2,
             "multivariate_feature_option": 2,
             "percentage_allocation": 0.0
           }
         ]
       },
       "changed_by": "admin@example.com",
       "timestamp": "2026-11-20T11:24:15.665049Z"
     }
   }
   ```
2. **`FLAG_DELETED`**:
   Fired when a feature state or flag is deleted.
3. **`AUDIT_LOG_CREATED`**:
   Fired on organization-level webhooks whenever an audit record is committed.
4. **`NEW_VERSION_PUBLISHED`**:
   Fired when environments use Versioning v2 (`use_v2_feature_versioning: true`) and a new draft version is published live.

---

## Architectural Directives for Guardian

1. **Decoupled Flag Model**: Rollout flags and Experiment flags must stay separate. Guardian will manage rollout flags through multivariate percentage allocations or standard segment overrides.
2. **Fast Guardrail Loop**: Guardian does not rely on Flagsmith's 5-minute experiment refresh throttle for emergency rollbacks. Guardian queries telemetry directly from ClickHouse warehouse events (`flagsmith_exp.events`) or Prometheus metrics on a sub-minute cadence.
3. **Immediate Killswitch**: On any guardrail breach, Guardian issues a direct `PATCH` setting `percentage_allocation: 0.0` and `enabled: false`, followed by dispatching alerts to Slack/webhook.
4. **Human in the Loop**: Final promotion from the last stage (e.g., 50% -> 100%) requires explicit human sign-off via Guardian's API/UI.
