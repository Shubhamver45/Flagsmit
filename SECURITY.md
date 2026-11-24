# Security Policy

## Historical Commit Notice

Early commits in this repository contain throwaway local keys and environment tokens from temporary, destroyed local test instances used during initial prototyping and scaffolding (such as local Flagsmith Docker instances on `localhost`). None of these keys grant access to any persistent, cloud, or production systems. All current configurations read credentials strictly from environment variables (`.env`, which is gitignored) or local configuration files (`config.yaml`, also gitignored). Known historical commit hashes are allowlisted in `.gitleaksignore`.

## Reporting Vulnerabilities

If you discover any security issues or vulnerabilities in Guardian or its deployment templates, please do not disclose them publicly. Open an advisory or reach out directly to the repository maintainer.
