# Kubernetes Example Helm Chart (`deploy/kubernetes/`)

> **Status:** Example chart — **untested on a real Kubernetes cluster**.

This minimal Helm v2 chart deploys Flagsmith Guardian as a single-replica (`Recreate` strategy) pod with:
- `GUARDIAN_ENV=production`
- A `PersistentVolumeClaim` mounted at `/app/data` for SQLite (`guardian.db`)
- A `Secret` for `GUARDIAN_API_TOKEN`, `FLAGSMITH_WEBHOOK_SECRET`, `FLAGSMITH_ADMIN_API_KEY`, and `FLAGSMITH_ENVIRONMENT_KEY`
- Non-root execution (`UID 10001`) with a read-only root filesystem, `/health`, and `/ready` probes

## Usage (Example)

```bash
helm lint ./deploy/kubernetes
helm template guardian ./deploy/kubernetes
helm upgrade --install guardian ./deploy/kubernetes \
  --set secrets.guardianApiToken="..." \
  --set secrets.flagsmithWebhookSecret="..." \
  --set secrets.flagsmithAdminApiKey="..." \
  --set secrets.flagsmithEnvironmentKey="..."
```
