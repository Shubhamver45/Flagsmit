# Local Demo Guide

Run the full interactive walkthrough (starts self-hosted Flagsmith, Postgres, Task Processor, Prometheus, Demo Checkout App, and Guardian):

```bash
./scripts/demo.sh
```
Or keep the stack running afterwards for manual dashboard inspection (`http://localhost:8080/`):
```bash
./scripts/demo.sh --serve
```
Or run the containerized demo stack:
```bash
docker compose -f compose.demo.yml up --build
```
Note: `./scripts/demo.sh` uses `DEMO_FORCE_RECALC=1` by default so Flagsmith Experimentation results compute immediately during a <2-minute walkthrough; unset `DEMO_FORCE_RECALC` (or set `DEMO_FORCE_RECALC=0`) to observe Flagsmith's 5-minute public `POST /results/refresh/` cooldown.
