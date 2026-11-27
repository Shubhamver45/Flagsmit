# Contributing to Guardian

Thank you for contributing to Guardian!

## Development Setup

Guardian targets **Python 3.11 and 3.12** (matching CI and `pyproject.toml`).

```bash
git clone https://github.com/Shubhamver45/flagsmith-guardian.git
cd flagsmith-guardian
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements-dev.txt
pip install -e .
```

## Running Tests & Verification

Before opening a pull request, run:

```bash
python -m pytest -v -m "not live" tests/
python scripts/check_readme_claims.py
python scripts/check_readme_links.py
```

## Security & Hygiene

- Never commit `.env`, API keys, webhook secrets, or database files (`*.db`).
- Any changes to authentication, webhooks, or status endpoints should preserve `GUARDIAN_ENV=production` fail-closed behavior and constant-time token comparison (`secrets.compare_digest`).
