"""Management command for Raspberry Pi image artifact workflows."""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.imager.models import RaspberryPiImageArtifact
from apps.imager.reservations import (
    DEFAULT_RESERVATION_PORTS,
    resolve_optional_env_bool,
    watch_reserved_nodes_loop,
    watch_reserved_nodes_once,
)
from apps.imager.services import (
    DEFAULT_RECOVERY_SSH_USER,
    ImagerBuildError,
    RecoveryAuthorizedKeyError,
    build_rpi4b_image,
    list_block_devices,
    normalize_recovery_authorized_key_line,
    prepare_image_serve,
    serve_image_file,
    test_rpi_access,
    write_image_to_device,
)


class Command(BaseCommand):
    """Build and list Raspberry Pi image artifacts for Arthexis."""

    help = "Build and safely write Raspberry Pi 4B image artifacts."

    def add_arguments(self, parser) -> None:
        """Register command actions and options."""

        subparsers = parser.add_subparsers(dest="action", required=True)

        build_parser = subparsers.add_parser("build", help="Build a Raspberry Pi 4B image artifact.")
        build_parser.add_argument("--name", required=True, help="Artifact name, for example v0-5-0.")
        build_parser.add_argument(
            "--base-image-uri",
            required=True,
            help="Base Raspberry Pi OS image URI (file://, local path, or https://).",
        )
        build_parser.add_argument(
            "--output-dir",
            default="build/rpi-imager",
            help="Output directory for generated image artifacts.",
        )
        build_parser.add_argument(
            "--download-base-uri",
            default="",
            help="Base URI where the generated image will be hosted for remote deploy.",
        )
        build_parser.add_argument(
            "--git-url",
            default="https://github.com/arthexis/arthexis.git",
            help="Git repository used for first-boot Arthexis bootstrap.",
        )
        build_parser.add_argument(
            "--skip-customize",
            action="store_true",
            help="Copy the base image without injecting bootstrap scripts.",
        )
        build_parser.add_argument(
            "--no-bundle-suite",
            action="store_true",
            help="Do not bundle a static copy of this Arthexis checkout into the image.",
        )
        build_parser.add_argument(
            "--suite-source",
            default="",
            help="Arthexis checkout path to bundle into the image (default: current suite base directory).",
        )
        build_parser.add_argument(
            "--copy-all-host-networks",
            action="store_true",
            help="Copy all host NetworkManager connection profiles, including saved credentials, into the image.",
        )
        build_parser.add_argument(
            "--copy-host-network",
            action="append",
            default=[],
            help="Copy one host NetworkManager profile by connection id, filename, or filename stem. May be repeated.",
        )
        build_parser.add_argument(
            "--host-network-profile-dir",
            default="",
            help="Host NetworkManager system-connections directory to read when copying network profiles.",
        )
        build_parser.add_argument(
            "--copy-parent-network",
            dest="copy_parent_network",
            action="store_true",
            default=None,
            help="Copy active parent Wi-Fi NetworkManager profiles into the image.",
        )
        build_parser.add_argument(
            "--no-copy-parent-network",
            dest="copy_parent_network",
            action="store_false",
            help="Disable IMAGER_COPY_PARENT_NETWORK_DEFAULT for this build.",
        )
        build_parser.add_argument(
            "--reserve",
            dest="reserve",
            action="store_true",
            default=None,
            help="Reserve a peer node row before first boot and bake its hostname into the image.",
        )
        build_parser.add_argument(
            "--no-reserve",
            dest="reserve",
            action="store_false",
            help="Disable IMAGER_RESERVE_DEFAULT for this build.",
        )
        build_parser.add_argument(
            "--reserve-number",
            type=int,
            default=None,
            help="Specific numeric suffix to reserve, for example 4 for gway-004.",
        )
        build_parser.add_argument(
            "--reserve-prefix",
            default="",
            help="Hostname prefix for reserved images. Defaults to the parent node prefix.",
        )
        build_parser.add_argument(
            "--reserve-role",
            default="",
            help="Optional node role name to assign to the reserved peer.",
        )
        build_parser.add_argument(
            "--build-engine",
            default="arthexis-bootstrap",
            help="Build engine backend used to produce the artifact.",
        )
        build_parser.add_argument(
            "--profile",
            default="bootstrap",
            help="Build profile for engine-specific validation and rollout metadata.",
        )
        build_parser.add_argument(
            "--profile-metadata",
            default="{}",
            help="JSON object carrying profile metadata, required artifacts, and rollout fields.",
        )
        build_parser.add_argument(
            "--recovery-ssh-user",
            default="",
            help=(
                "Recovery SSH username baked into the image when recovery keys are provided via --recovery-authorized-key-file or --recovery-authorized-key "
                f"(default: {DEFAULT_RECOVERY_SSH_USER})."
            ),
        )
        build_parser.add_argument(
            "--recovery-authorized-key-file",
            action="append",
            default=[],
            help="Path to a public-key file to authorize for recovery SSH access. May be repeated.",
        )
        build_parser.add_argument(
            "--recovery-authorized-key",
            action="append",
            default=[],
            help=(
                "Inline OpenSSH public key to authorize for recovery SSH access. "
                "May be repeated to avoid bundling key material in repository files."
            ),
        )
        build_parser.add_argument(
            "--skip-recovery-ssh",
            action="store_true",
            help="Intentionally disable recovery SSH setup for this build.",
        )

        subparsers.add_parser("devices", help="List candidate block devices for image writing.")
        subparsers.add_parser("list", help="List generated Raspberry Pi image artifacts.")

        write_parser = subparsers.add_parser(
            "write",
            help="Write an existing image artifact (or local image path) to a block device.",
        )
        write_parser.add_argument("--artifact", default="", help="Registered artifact name to write.")
        write_parser.add_argument(
            "--image-path",
            default="",
            help="Direct local path to an image file to write (alternative to --artifact).",
        )
        write_parser.add_argument("--device", required=True, help="Target block device path, for example /dev/sdb.")
        write_parser.add_argument(
            "--yes",
            action="store_true",
            help="Confirm destructive write operation.",
        )

        serve_parser = subparsers.add_parser(
            "serve",
            help="Serve an existing image artifact over HTTP and print its deployment URL.",
        )
        serve_parser.add_argument("--artifact", default="", help="Registered artifact name to serve.")
        serve_parser.add_argument(
            "--image-path",
            default="",
            help="Direct local path to an image file to serve (alternative to --artifact).",
        )
        serve_parser.add_argument("--host", default="0.0.0.0", help="Interface to bind for serving.")
        serve_parser.add_argument("--port", type=int, default=8088, help="TCP port to bind for serving.")
        serve_parser.add_argument(
            "--url-host",
            default="",
            help="Host/IP advertised in the generated URL. Use the address reachable by target devices.",
        )
        serve_parser.add_argument(
            "--base-url",
            default="",
            help="Full base URL to advertise instead of composing one from --url-host and --port.",
        )
        serve_parser.add_argument(
            "--no-update-artifact-url",
            action="store_true",
            help="Do not persist the generated URL on the artifact record.",
        )

        access_parser = subparsers.add_parser(
            "test-access",
            help="Test SSH and HTTP access to a burned Raspberry Pi image after it boots.",
        )
        access_parser.add_argument("--host", required=True, help="RPi hostname or IP address.")
        access_parser.add_argument(
            "--ssh-user",
            default=DEFAULT_RECOVERY_SSH_USER,
            help=f"Recovery SSH username to test (default: {DEFAULT_RECOVERY_SSH_USER}).",
        )
        access_parser.add_argument("--ssh-port", type=int, default=22, help="SSH port to test.")
        access_parser.add_argument("--ssh-key", default="", help="Private key path for SSH auth testing.")
        access_parser.add_argument(
            "--http-url",
            default="",
            help="Suite URL to test. Defaults to http://HOST:8888/ when HTTP checks are enabled.",
        )
        access_parser.add_argument("--http-port", type=int, default=8888, help="Default suite HTTP port.")
        access_parser.add_argument("--timeout", type=float, default=5.0, help="Per-check timeout in seconds.")
        access_parser.add_argument("--skip-ssh", action="store_true", help="Skip SSH TCP/auth checks.")
        access_parser.add_argument("--skip-http", action="store_true", help="Skip HTTP suite reachability check.")

        watch_parser = subparsers.add_parser(
            "watch-reservations",
            help="Watch reserved image nodes on wlanX/eth0 and clear reservations after first contact.",
        )
        watch_parser.add_argument(
            "--interfaces",
            default="",
            help="Comma-separated interfaces to watch. Defaults to IMAGER_RESERVATION_WATCH_INTERFACES or active wlanX plus eth0.",
        )
        watch_parser.add_argument(
            "--ports",
            default=",".join(str(port) for port in DEFAULT_RESERVATION_PORTS),
            help="Comma-separated /nodes/info/ ports to probe.",
        )
        watch_parser.add_argument("--timeout", type=float, default=1.5, help="Per-probe timeout in seconds.")
        watch_parser.add_argument("--interval", type=float, default=30.0, help="Loop interval in seconds.")
        watch_parser.add_argument("--once", action="store_true", help="Run one watch pass and exit.")

    def handle(self, *args, **options) -> None:
        """Dispatch command to selected action."""

        action = options["action"]
        if action == "build":
            self._handle_build(options)
            return
        if action == "list":
            self._handle_list()
            return
        if action == "devices":
            self._handle_devices()
            return
        if action == "write":
            self._handle_write(options)
            return
        if action == "serve":
            self._handle_serve(options)
            return
        if action == "test-access":
            self._handle_test_access(options)
            return
        if action == "watch-reservations":
            self._handle_watch_reservations(options)
            return
        raise CommandError(f"Unsupported action '{action}'.")

    def _handle_build(self, options: dict[str, object]) -> None:
        """Build a Raspberry Pi 4B image artifact and print summary metadata."""

        try:
            profile_metadata = json.loads(str(options["profile_metadata"]))
        except json.JSONDecodeError as exc:
            raise CommandError("--profile-metadata must be valid JSON.") from exc
        if not isinstance(profile_metadata, dict):
            raise CommandError("--profile-metadata must decode to a JSON object.")

        recovery_authorized_keys = self._read_recovery_authorized_keys(
            file_paths=[str(path) for path in options.get("recovery_authorized_key_file", [])],
            inline_keys=[str(key) for key in options.get("recovery_authorized_key", [])],
        )
        skip_recovery_ssh = options["skip_recovery_ssh"]
        customize = not options["skip_customize"]
        recovery_ssh_user = str(options["recovery_ssh_user"]).strip()
        if skip_recovery_ssh and (recovery_authorized_keys or recovery_ssh_user):
            raise CommandError(
                "--skip-recovery-ssh cannot be combined with recovery SSH key options or --recovery-ssh-user."
            )
        if customize and not skip_recovery_ssh and not recovery_authorized_keys:
            raise CommandError(
                "Recovery SSH is required for customized image builds. "
                "Provide --recovery-authorized-key-file/--recovery-authorized-key or pass --skip-recovery-ssh to opt out."
            )
        if recovery_authorized_keys:
            recovery_ssh_user = recovery_ssh_user or DEFAULT_RECOVERY_SSH_USER
        reserve_node = resolve_optional_env_bool(
            options.get("reserve"),
            "IMAGER_RESERVE_DEFAULT",
            default=False,
        )
        copy_parent_networks = resolve_optional_env_bool(
            options.get("copy_parent_network"),
            "IMAGER_COPY_PARENT_NETWORK_DEFAULT",
            default=False,
        )
        reserve_number = options.get("reserve_number")
        if reserve_number is not None and int(reserve_number) <= 0:
            raise CommandError("--reserve-number must be greater than zero.")

        try:
            result = build_rpi4b_image(
                name=str(options["name"]),
                base_image_uri=str(options["base_image_uri"]),
                output_dir=Path(str(options["output_dir"])),
                download_base_uri=str(options["download_base_uri"]),
                git_url=str(options["git_url"]),
                customize=customize,
                build_engine=str(options["build_engine"]),
                profile=str(options["profile"]),
                profile_metadata=profile_metadata,
                recovery_ssh_user=recovery_ssh_user,
                recovery_authorized_keys=recovery_authorized_keys,
                skip_recovery_ssh=bool(skip_recovery_ssh),
                bundle_suite=not bool(options["no_bundle_suite"]),
                suite_source_path=Path(str(options["suite_source"]))
                if str(options["suite_source"]).strip()
                else None,
                copy_all_host_networks=bool(options["copy_all_host_networks"]),
                host_network_names=[
                    str(name)
                    for name in options.get("copy_host_network", [])
                    if str(name).strip()
                ],
                host_network_profile_dir=Path(str(options["host_network_profile_dir"]))
                if str(options["host_network_profile_dir"]).strip()
                else None,
                copy_parent_networks=copy_parent_networks,
                reserve_node=reserve_node,
                reserve_hostname_prefix=str(options["reserve_prefix"]),
                reserve_number=reserve_number,
                reserve_role=str(options["reserve_role"]),
            )
        except ImagerBuildError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(f"Built image: {result.output_path}"))
        self.stdout.write(f"sha256={result.sha256}")
        self.stdout.write(f"size_bytes={result.size_bytes}")
        if result.download_uri:
            self.stdout.write(f"download_uri={result.download_uri}")
        if customize and skip_recovery_ssh:
            self.stdout.write("recovery_ssh=disabled (--skip-recovery-ssh)")
        reservation = getattr(result, "reservation", None)
        if reservation:
            self.stdout.write(
                "reserved_node="
                f"{reservation.get('hostname')} "
                f"address={reservation.get('ipv4_address') or '(none)'} "
                f"id={reservation.get('node_id')}"
            )

    def _read_recovery_authorized_keys(
        self,
        *,
        file_paths: list[str],
        inline_keys: list[str],
    ) -> list[str]:
        """Load recovery authorized keys from file and inline command options."""

        keys: list[str] = []
        for raw_path in file_paths:
            path = Path(raw_path).expanduser()
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError) as exc:
                raise CommandError(
                    f"Could not read recovery authorized key file '{path}': {exc}"
                ) from exc
            for line_number, line in enumerate(lines, start=1):
                self._append_recovery_key_line(
                    keys=keys,
                    source=f"{path}:{line_number}",
                    line=line,
                )

        for key_number, key_line in enumerate(inline_keys, start=1):
            self._append_recovery_key_line(
                keys=keys,
                source=f"--recovery-authorized-key[{key_number}]",
                line=key_line,
            )

        if (file_paths or inline_keys) and not keys:
            raise CommandError("Recovery authorized key inputs did not contain any usable public keys.")
        return keys

    def _append_recovery_key_line(self, *, keys: list[str], source: str, line: str) -> None:
        """Normalize and append a single recovery authorized-key line when valid."""

        try:
            normalized = normalize_recovery_authorized_key_line(line)
        except RecoveryAuthorizedKeyError as exc:
            self.stderr.write(
                self.style.WARNING(
                    f"Skipping {exc} from {source}."
                )
            )
            return
        if normalized:
            keys.append(normalized)

    def _handle_list(self) -> None:
        """Print known Raspberry Pi image artifacts."""

        artifacts = RaspberryPiImageArtifact.objects.order_by("-created_at", "name")
        if not artifacts:
            self.stdout.write("No Raspberry Pi image artifacts are registered.")
            return
        for artifact in artifacts:
            self.stdout.write(
                f"{artifact.name} [{artifact.target}] file={artifact.output_filename} "
                f"sha256={artifact.sha256} uri={artifact.download_uri or '(not configured)'}"
            )

    def _handle_devices(self) -> None:
        """Print block devices and safety metadata for writing."""

        try:
            devices = list_block_devices()
        except ImagerBuildError as exc:
            raise CommandError(str(exc)) from exc

        if not devices:
            self.stdout.write("No block devices were discovered.")
            return
        for device in devices:
            mountpoints = ",".join(device.mountpoints) if device.mountpoints else "(none)"
            partitions = ",".join(device.partitions) if device.partitions else "(none)"
            self.stdout.write(
                f"{device.path} size={device.size_bytes} transport={device.transport or '(unknown)'} "
                f"removable={'yes' if device.removable else 'no'} protected={'yes' if device.protected else 'no'} "
                f"partitions={partitions} mounts={mountpoints}"
            )

    def _handle_write(self, options: dict[str, object]) -> None:
        """Write image artifact to block device with safety checks and verification."""

        artifact_name = str(options["artifact"])
        image_path = str(options["image_path"])
        if bool(artifact_name) == bool(image_path):
            raise CommandError("Provide exactly one of --artifact or --image-path.")

        try:
            result = write_image_to_device(
                device_path=str(options["device"]),
                artifact_name=artifact_name,
                image_path=image_path,
                confirmed=bool(options["yes"]),
            )
        except ImagerBuildError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(f"Wrote {result.image_path} -> {result.device_path}"))
        self.stdout.write(f"size_bytes={result.size_bytes}")
        self.stdout.write(f"source_sha256={result.source_sha256}")
        self.stdout.write(f"written_sha256={result.written_sha256}")
        self.stdout.write(f"verified={'yes' if result.verified else 'no'}")

    def _handle_serve(self, options: dict[str, object]) -> None:
        """Serve an image artifact over HTTP for deployment workflows."""

        artifact_name = str(options["artifact"])
        image_path = str(options["image_path"])
        if bool(artifact_name) == bool(image_path):
            raise CommandError("Provide exactly one of --artifact or --image-path.")

        try:
            result = prepare_image_serve(
                artifact_name=artifact_name,
                image_path=image_path,
                host=str(options["host"]),
                port=int(options["port"]),
                url_host=str(options["url_host"]),
                base_url=str(options["base_url"]),
                update_artifact_url=not bool(options["no_update_artifact_url"]),
            )
        except ImagerBuildError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(f"Serving image: {result.image_path}"))
        self.stdout.write(f"artifact_url={result.url}")
        self.stdout.write("Press Ctrl+C to stop serving.")
        try:
            serve_image_file(image_path=result.image_path, host=result.host, port=result.port)
        except KeyboardInterrupt:
            self.stdout.write("Stopped image server.")
        except OSError as exc:
            raise CommandError(f"Could not start image server: {exc}") from exc

    def _handle_test_access(self, options: dict[str, object]) -> None:
        """Test access to an installed Raspberry Pi image."""

        try:
            result = test_rpi_access(
                host=str(options["host"]),
                ssh_user=str(options["ssh_user"]),
                ssh_port=int(options["ssh_port"]),
                ssh_key=str(options["ssh_key"]),
                http_url=str(options["http_url"]),
                http_port=int(options["http_port"]),
                timeout=float(options["timeout"]),
                skip_ssh=bool(options["skip_ssh"]),
                skip_http=bool(options["skip_http"]),
            )
        except ImagerBuildError as exc:
            raise CommandError(str(exc)) from exc

        for check in result.checks:
            status = "ok" if check.ok else "failed"
            self.stdout.write(f"{check.name}={status} {check.detail}")
        if not result.ok:
            raise CommandError(f"RPi access test failed for {result.host}.")
        self.stdout.write(self.style.SUCCESS(f"RPi access test passed for {result.host}."))

    def _handle_watch_reservations(self, options: dict[str, object]) -> None:
        """Watch reserved nodes and clear reservations after first contact."""

        interfaces = [
            token.strip()
            for token in str(options["interfaces"]).split(",")
            if token.strip()
        ] or None
        ports = self._parse_ports(str(options["ports"]))
        timeout = float(options["timeout"])
        interval = float(options["interval"])

        if options["once"]:
            result_sets = [
                watch_reserved_nodes_once(
                    interfaces=interfaces,
                    ports=ports,
                    timeout=timeout,
                )
            ]
        else:
            result_sets = watch_reserved_nodes_loop(
                interfaces=interfaces,
                ports=ports,
                timeout=timeout,
                interval=interval,
            )

        for results in result_sets:
            if not results:
                self.stdout.write("reserved_nodes=none")
            for result in results:
                detail = f" {result.detail}" if result.detail else ""
                self.stdout.write(
                    f"{result.hostname} id={result.node_id} status={result.status}{detail}"
                )
            if options["once"]:
                return

    def _parse_ports(self, raw_value: str) -> tuple[int, ...]:
        ports: list[int] = []
        for token in raw_value.split(","):
            token = token.strip()
            if not token:
                continue
            try:
                port = int(token)
            except ValueError as exc:
                raise CommandError(f"Invalid port: {token}") from exc
            if not 1 <= port <= 65535:
                raise CommandError(f"Port out of range: {port}")
            ports.append(port)
        if not ports:
            raise CommandError("At least one port is required.")
        return tuple(ports)
