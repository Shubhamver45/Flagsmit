# Deployment Guide

## Single-Instance Architecture Note
Guardian uses local SQLite (`guardian.db`) with `WAL` mode and is designed as a single-instance controller. Horizontal multi-replica clustering is not supported.

## Docker / Compose
Use `Dockerfile` (non-root `UID 10001`, read-only root FS compatible, `/app/data` volume) or `compose.demo.yml` for local evaluation.

## Render
[render.yaml](../render.yaml) configures `GUARDIAN_ENV=production` and `GUARDIAN_CONFIG_PATH=/app/config.render.yaml` (`metric_source: push`). Note that Render free-tier web services do not provide a persistent disk across redeploys; attach a persistent disk in paid plans if SQLite state must survive restarts.

## Kubernetes (Example, Untested on a Live Cluster)
See [deploy/kubernetes/](../deploy/kubernetes/) for an example Helm v2 chart with a `PersistentVolumeClaim`, `Secret`, `GUARDIAN_ENV=production`, and `/health` + `/ready` probes.
