# Security Policy

## Reporting a Vulnerability

We appreciate security researchers and users who report vulnerabilities responsibly.

- **Do not** open public GitHub issues for security reports.
- Email details to **tecnologia@gelectriic.com** with the subject line `SECURITY REPORT: Arthexis Constellation`.
- Include steps to reproduce, affected versions or deployments, and any proof-of-concept code.
- If you have a suggested fix or mitigation, include it.

We will acknowledge receipt within **5 business days** and provide a status update as we investigate.

## Supported Versions

Security fixes are applied to the latest release on the `main` branch. If you operate an older deployment, please plan to upgrade using the project’s upgrade scripts and release cadence documentation.

## Coordinated Disclosure

Please allow us a reasonable window to investigate and remediate before public disclosure. We will coordinate a timeline with you once we confirm the issue.

## Security Best Practices for Operators

- Keep your deployment updated using the provided upgrade scripts.
- Restrict admin access and rotate credentials regularly.
- Use HTTPS and ensure that security headers are enabled at your reverse proxy.
- Store secrets outside of version control and rotate them if exposure is suspected.
