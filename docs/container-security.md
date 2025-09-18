# Container Security Scanning

The CI pipeline runs a `container-scan` job that builds the Docker image defined in [`Dockerfile`](../Dockerfile) for the `linux/arm64` platform and scans it for known vulnerabilities using [Trivy](https://github.com/aquasecurity/trivy). The scanner downloads and caches its vulnerability database between runs to speed up subsequent checks.

## Remediation expectations

* The job fails if any HIGH or CRITICAL vulnerabilities are reported. Address alerts by updating the Docker base image, rebuilding third-party dependencies, or upgrading the affected Python/system packages.
* After applying fixes, rebuild the image locally (`docker buildx build --platform linux/arm64 .`) to ensure the container still builds, then re-run the CI pipeline or the Trivy scan locally (`trivy image <local-tag>`) to confirm the vulnerabilities are resolved.
* If no vendor fix is available, track the issue and document the mitigation in the security backlog before temporarily suppressing it.

## Local scanning tips

1. Enable Docker Buildx and QEMU locally if you need to target the ARM64 platform (`docker buildx create --use`).
2. Build the image with a temporary tag: `docker buildx build --platform linux/arm64 -t arthexis-local:scan --load .`.
3. Run `trivy image arthexis-local:scan --severity HIGH,CRITICAL` to mirror the CI behaviour.
4. Remove the temporary image afterwards (`docker image rm arthexis-local:scan`).
