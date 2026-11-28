# Getting Support for Flagsmith Guardian

## Where to Ask Questions
- **Installation, configuration, and usage questions:** Open a thread in [GitHub Discussions](https://github.com/Shubhamver45/flagsmith-guardian/discussions) (or an issue tagged `question` if Discussions is not enabled).
- **5-minute setup walkthrough:** See [docs/bring-your-own-app.md](docs/bring-your-own-app.md).

## Where to Report Bugs
- Open a [GitHub Issue](https://github.com/Shubhamver45/flagsmith-guardian/issues/new).
- Please include:
  1. Guardian version or commit SHA (`git rev-parse --short HEAD`) and Python/Docker runtime.
  2. Flagsmith version and deployment type (self-hosted Docker Compose, Kubernetes, etc.).
  3. Your sanitized `config.yaml` (remove all tokens, API keys, and webhook secrets).
  4. Relevant output from `GET /status` and Guardian logs.

## Security Vulnerabilities
- Do **not** open public issues for security vulnerabilities. Follow [SECURITY.md](SECURITY.md).
