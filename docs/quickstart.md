# Quickstart Guide

## Option 1: One-Command Full Local Demo (Docker)
```bash
git clone https://github.com/Shubhamver45/flagsmith-guardian.git
cd flagsmith-guardian
cp .env.example .env
docker compose -f compose.demo.yml up --build
```
- Guardian Dashboard: `http://localhost:8080/`
- Flagsmith UI: `http://localhost:8000/`
- Shutdown: `docker compose -f compose.demo.yml down`
- Full Reset (clears volumes): `docker compose -f compose.demo.yml down -v`

## Option 2: Python Package & CLI (Python >=3.11)
```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
guardian init
guardian doctor
guardian config validate
python3 -m guardian.main --host 127.0.0.1 --port 8080
```
