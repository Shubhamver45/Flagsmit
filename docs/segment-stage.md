# Optional Segment Rollout Stage (`segment`)

By default, Guardian starts rollouts directly at the first percentage stage (`STAGE_1`). You can optionally enable a **Segment Stage** (`SEGMENT_STAGE`) that targets a Flagsmith segment (for example `internal-team`) **before** any percentage rollout begins.

This feature is **off by default** and has zero effect unless `segment` is configured.

---

## Configuration (`config.yaml`)

```yaml
flag_name: "new_checkout"
segment: "internal-team"     # optional; omit or leave empty to disable
segment_value: "new_flow"    # optional Flagsmith feature_state_value for the override
stages: [1, 5, 25, 100]
soak_time_seconds: 60
```

### How It Works
1. **Create the segment in Flagsmith** (e.g., `internal-team` matching `email` or `user_id` rules).
2. **`PENDING` -> `SEGMENT_STAGE` (`0%`):** Guardian creates/enables a Flagsmith segment override for `segment` via the Flagsmith Admin API while keeping global percentage allocation at `0%`.
3. **`SEGMENT_STAGE` -> `STAGE_1`:** Once the soak timer completes and all guardrails are healthy, Guardian advances to the first percentage stage.
4. **Rollback:** If any guardrail breaches (or an operator triggers manual rollback / reset), Guardian rolls the global flag back to `0%` **and removes the segment override** so segment members also return to the safe experience.

> **Echo-Window Limitation:** Guardian records the segment override write in its ledger so Flagsmith's `FLAG_UPDATED` webhook echo is ignored within `echo_window_seconds` (default `30s`); if webhook delivery is delayed beyond `echo_window_seconds`, a transient drift warning may still fire.
