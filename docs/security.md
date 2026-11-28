# Security Architecture

See [SECURITY.md](../SECURITY.md) for disclosure policy and details. Implemented controls (all covered by `tests/test_security.py`):
- **Fail-closed production mode (`GUARDIAN_ENV=production`):** Refuses boot without `GUARDIAN_API_TOKEN` and rejects Flagsmith webhooks without `FLAGSMITH_WEBHOOK_SECRET`.
- **Streaming 64 KB request limit:** `RequestSizeLimitMiddleware` enforces 65,536-byte limits on both `Content-Length` and chunked/streaming requests (`413 Payload Too Large`).
- **Authentication & CORS:** Constant-time `secrets.compare_digest` on `Authorization: Bearer` / `X-Guardian-Token`, query-parameter tokens forbidden, and explicit `GUARDIAN_CORS_ORIGINS` allowlist.
- **Signed Webhooks & Replay Protection:** HMAC-SHA256 `X-Flagsmith-Signature` verification, optional `X-Flagsmith-Timestamp` 300s freshness + replay rejection (`409`), and fail-closed `X-Slack-Signature` (`SLACK_SIGNING_SECRET`) on `POST /webhooks/slack`.
- **Tamper-Evident Audit Log:** SHA-256 hash chaining (`prev_hash` -> `entry_hash`) across `audit_log` rows verified via `GET /audit/verify` and `guardian audit verify`.
