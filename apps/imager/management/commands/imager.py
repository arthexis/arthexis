"""Management command for Raspberry Pi image artifact workflows."""

import json
import os
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.cards.initial_profile import InitialProfileError
from apps.imager.burner import (
    format_job_status,
    queue_burn_job,
    work_loop,
    work_once,
)
from apps.imager.constants import (
    DEFAULT_ARTHEXIS_GIT_URL,
    UNIVERSAL_CONNECT_UPDATE_REQUIRED_ARTIFACTS,
    UNIVERSAL_CONNECT_UPDATE_ROLES,
)
from apps.imager.initial_profile import load_initial_profile, reconcile_initial_profile
from apps.imager.models import RaspberryPiImageArtifact, RaspberryPiImageBurnJob
from apps.imager.reservations import (
    DEFAULT_RESERVATION_PORTS,
    GWAY_HOSTNAME_PREFIX,
    RemoteReservationError,
    default_gway_downstream_registration_base_url,
    default_gway_next_number_base_url,
    next_reservation,
    resolve_optional_env_bool,
    watch_reserved_nodes_loop,
    watch_reserved_nodes_once,
)
from apps.imager.services import (
    DEFAULT_CUSTOMIZED_IMAGE_MINIMUM_SIZE_GIB,
    DEFAULT_IMAGE_WRITE_BACKUP_DIR,
    DEFAULT_RECOVERY_SSH_USER,
    IMAGE_SIZE_BYTES_PER_GIB,
    STORAGE_BACKEND_LOCAL,
    SUPPORTED_STORAGE_BACKENDS,
    ImagerBuildError,
    RecoveryAuthorizedKeyError,
    _ensure_image_minimum_size,
    _resolve_base_image,
    build_rpi4b_image,
    list_block_devices,
    normalize_recovery_authorized_key_line,
    prepare_image_serve,
    serve_image_file,
    test_rpi_access,
    write_image_to_device,
)
from apps.imager.usb_stability import quiet_usb_pollers


class Command(BaseCommand):
    """Build and list Raspberry Pi image artifacts for Arthexis."""

    help = "Build and safely write Raspberry Pi 4B image artifacts."

    def add_arguments(self, parser) -> None:
        """Register command actions and options."""

        subparsers = parser.add_subparsers(dest="action", required=True)

        build_parser = subparsers.add_parser(
            "build", help="Build a Raspberry Pi 4B image artifact."
        )
        build_parser.add_argument(
            "--name", required=True, help="Artifact name, for example v0-5-0."
        )
        initial_profile_parser = subparsers.add_parser(
            "initial-profile",
            help="Validate a private first-boot image profile, or apply it explicitly.",
        )
        initial_profile_parser.add_argument(
            "--profile", type=Path, required=True, help="Private initial TOML profile."
        )
        initial_profile_parser.add_argument(
            "--apply",
            action="store_true",
            help=(
                "Reconcile the profile into this host. Intended for the image's "
                "first-boot service; without this flag the command only validates."
            ),
        )
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
            default=DEFAULT_ARTHEXIS_GIT_URL,
            help=(
                "Optional authenticated Git repository used for first-boot Arthexis "
                "bootstrap when the bundled suite is unavailable."
            ),
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
        connect_bootstrap_group = build_parser.add_mutually_exclusive_group()
        connect_bootstrap_group.add_argument(
            "--enable-connect-bootstrap",
            action="store_true",
            help="Bake in Raspberry Pi Connect bootstrap for this image.",
        )
        connect_bootstrap_group.add_argument(
            "--skip-connect-bootstrap",
            action="store_true",
            help=(
                "Do not bake in Raspberry Pi Connect bootstrap. Reserved field "
                "nodes enable it by default."
            ),
        )
        build_parser.add_argument(
            "--minimum-image-size-gib",
            type=int,
            default=None,
            help=(
                "Minimum raw image size in GiB before customization. "
                f"Customized builds default to {DEFAULT_CUSTOMIZED_IMAGE_MINIMUM_SIZE_GIB}; "
                "use 0 to preserve the base image size."
            ),
        )
        build_parser.add_argument(
            "--suite-source",
            default="",
            help="Arthexis checkout path to bundle into the image (default: current suite base directory).",
        )
        build_parser.add_argument(
            "--initial-profile",
            default="",
            help="TOML profile embedded for idempotent first-boot configuration.",
        )
        build_parser.add_argument(
            "--connect-auth-key-file",
            "--connect-auth-config",
            dest="connect_auth_key_file",
            default="",
            help=(
                "Mode-0600 raw or TOML file containing [rpi_connect].auth_key "
                "to sign the image into Raspberry Pi Connect on first boot."
            ),
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
            "--next-number-base-url",
            default="",
            help="Optional upstream base URL used to resolve the next reservation number before falling back locally.",
        )
        build_parser.add_argument(
            "--downstream-registration-base-url",
            default="",
            help="Optional upstream base URL baked into reserved images for first-boot downstream registration.",
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
        supported_storage_backends = tuple(sorted(SUPPORTED_STORAGE_BACKENDS))
        build_parser.add_argument(
            "--storage-backend",
            choices=supported_storage_backends,
            default=STORAGE_BACKEND_LOCAL,
            help=_(
                "Artifact storage backend stub (%(supported)s). Local keeps artifacts on the build host."
            )
            % {"supported": ", ".join(supported_storage_backends)},
        )
        build_parser.add_argument(
            "--storage-options",
            default="{}",
            help="JSON object with backend-specific storage options reserved for future external upload support.",
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

        gway_parser = subparsers.add_parser(
            "gway-burn",
            help="Build a reserved GWAY image and queue it for the burner when configured.",
        )
        gway_parser.add_argument(
            "--base-image-uri",
            default="",
            help="Base Raspberry Pi OS image URI. Defaults to IMAGER_GWAY_BASE_IMAGE_URI.",
        )
        gway_parser.add_argument(
            "--name",
            default="",
            help="Artifact name. Defaults to gway-### using the selected reservation number.",
        )
        gway_parser.add_argument(
            "--output-dir",
            default="build/rpi-imager",
            help="Output directory for generated image artifacts.",
        )
        gway_parser.add_argument(
            "--download-base-uri",
            default="",
            help="Base URI where the generated image will be hosted for remote deploy.",
        )
        gway_parser.add_argument(
            "--git-url",
            default=DEFAULT_ARTHEXIS_GIT_URL,
            help=(
                "Optional authenticated Git repository used for first-boot Arthexis "
                "bootstrap when the bundled suite is unavailable."
            ),
        )
        gway_parser.add_argument(
            "--reserve-number",
            type=int,
            default=None,
            help="Manual numeric GWAY suffix, for example 4 for gway-004.",
        )
        gway_parser.add_argument(
            "--reserve-role",
            default=os.environ.get("IMAGER_GWAY_RESERVE_ROLE", "Control"),
            help="Node role name assigned to the reserved GWAY placeholder.",
        )
        gway_parser.add_argument(
            "--next-number-base-url",
            default="",
            help=(
                "Optional upstream base URL used to determine the next GWAY "
                "number. Defaults to local offline numbering."
            ),
        )
        gway_parser.add_argument(
            "--downstream-registration-base-url",
            default="",
            help=(
                "Optional upstream base URL baked into the image for "
                "first-boot downstream registration. Omitted by default."
            ),
        )
        gway_parser.add_argument(
            "--device",
            default="",
            help="Stable burner device path. Defaults to IMAGER_GWAY_BURN_DEVICE or IMAGER_BURN_DEVICE.",
        )
        gway_parser.add_argument(
            "--backup",
            action="store_true",
            help="Back up the target media before the queued burn.",
        )
        gway_parser.add_argument(
            "--backup-dir",
            default=DEFAULT_IMAGE_WRITE_BACKUP_DIR,
            help="Directory used for optional burner backups.",
        )
        gway_parser.add_argument(
            "--minimum-image-size-gib",
            type=int,
            default=None,
            help=(
                "Minimum raw image size in GiB before customization. "
                f"Customized builds default to {DEFAULT_CUSTOMIZED_IMAGE_MINIMUM_SIZE_GIB}; "
                "use 0 to preserve the base image size."
            ),
        )
        gway_parser.add_argument(
            "--suite-source",
            default="",
            help="Arthexis checkout path to bundle into the image (default: current suite base directory).",
        )
        gway_parser.add_argument(
            "--skip-recovery-ssh",
            action="store_true",
            help="Intentionally disable recovery SSH setup for this build.",
        )
        gway_parser.add_argument(
            "--skip-connect-bootstrap",
            action="store_true",
            help="Do not install and enable Raspberry Pi Connect during first-boot GWAY bootstrap.",
        )
        gway_parser.add_argument(
            "--connect-auth-key-file",
            "--connect-auth-config",
            dest="connect_auth_key_file",
            default="",
            help=(
                "Mode-0600 raw or TOML file containing [rpi_connect].auth_key "
                "to sign the image into Raspberry Pi Connect on first boot."
            ),
        )
        gway_parser.add_argument(
            "--recovery-ssh-user",
            default="",
            help=(
                "Recovery SSH username baked into the image when recovery keys are provided "
                f"(default: {DEFAULT_RECOVERY_SSH_USER})."
            ),
        )
        gway_parser.add_argument(
            "--recovery-authorized-key-file",
            action="append",
            default=[],
            help="Path to a public-key file to authorize for recovery SSH access. May be repeated.",
        )
        gway_parser.add_argument(
            "--recovery-authorized-key",
            action="append",
            default=[],
            help="Inline OpenSSH public key to authorize for recovery SSH access. May be repeated.",
        )

        subparsers.add_parser(
            "devices", help="List candidate block devices for image writing."
        )
        subparsers.add_parser(
            "list", help="List generated Raspberry Pi image artifacts."
        )

        register_connect_parser = subparsers.add_parser(
            "register-connect-release",
            help="Register a connect-ota image artifact as a universal Raspberry Connect update release.",
        )
        register_connect_parser.add_argument(
            "--artifact",
            required=True,
            help="Registered Raspberry Pi image artifact name.",
        )
        register_connect_parser.add_argument(
            "--name",
            default="",
            help="Connect image release name. Defaults to the artifact name.",
        )
        register_connect_parser.add_argument(
            "--version",
            default="",
            help="Release version. Defaults to the artifact profile manifest release_version.",
        )
        register_connect_parser.add_argument(
            "--artifact-url",
            default="",
            help="Public artifact URL. Defaults to the artifact download_uri.",
        )
        register_connect_parser.add_argument(
            "--compatibility-tag",
            action="append",
            default=[],
            help="Additional compatibility tag. May be repeated and accepts comma, semicolon, or whitespace separated values.",
        )
        register_connect_parser.add_argument(
            "--retention-days",
            type=int,
            default=30,
            help="Documented artifact retention policy in days.",
        )

        write_parser = subparsers.add_parser(
            "write",
            help="Write an existing image artifact (or local image path) to a block device.",
        )
        write_parser.add_argument(
            "--artifact", default="", help="Registered artifact name to write."
        )
        write_parser.add_argument(
            "--image-path",
            default="",
            help="Direct local path to an image file to write (alternative to --artifact).",
        )
        write_parser.add_argument(
            "--device",
            required=True,
            help="Target block device path, for example /dev/sdb.",
        )
        write_parser.add_argument(
            "--yes",
            action="store_true",
            help="Confirm destructive write operation.",
        )
        write_parser.add_argument(
            "--backup",
            action="store_true",
            help="Back up and verify the current target media before writing.",
        )
        write_parser.add_argument(
            "--backup-dir",
            default="",
            help=(
                "Directory for --backup images. Relative paths are resolved under "
                f"the suite root. Default: {DEFAULT_IMAGE_WRITE_BACKUP_DIR}."
            ),
        )
        write_parser.add_argument(
            "--no-quiet-usb",
            action="store_true",
            help=(
                "Do not pause Control-node USB pollers and desktop disk monitors "
                "during this write."
            ),
        )
        write_parser.add_argument(
            "--no-windows-automount-guard",
            action="store_true",
            help=(
                "Do not temporarily disable Windows automount during this write. "
                "Only use when external controls prevent the target from remounting."
            ),
        )

        burn_parser = subparsers.add_parser(
            "burn",
            help="Queue and monitor durable SD-card burn jobs.",
        )
        burn_subparsers = burn_parser.add_subparsers(dest="burn_action", required=True)
        burn_queue_parser = burn_subparsers.add_parser(
            "queue",
            help="Queue a destructive SD-card burn for the service worker.",
        )
        burn_queue_parser.add_argument(
            "--artifact", default="", help="Registered artifact name to burn."
        )
        burn_queue_parser.add_argument(
            "--image",
            "--image-path",
            dest="image_path",
            default="",
            help="Direct local path to an image file to burn.",
        )
        burn_queue_parser.add_argument(
            "--device",
            required=True,
            help="Target block device path, for example /dev/sdb.",
        )
        burn_queue_parser.add_argument(
            "--backup",
            action="store_true",
            help="Back up and verify the current target media before writing.",
        )
        burn_queue_parser.add_argument(
            "--backup-dir",
            default="",
            help=(
                "Directory for --backup images. Relative paths are resolved under "
                f"the suite root. Default: {DEFAULT_IMAGE_WRITE_BACKUP_DIR}."
            ),
        )
        burn_status_parser = burn_subparsers.add_parser(
            "status",
            help="Show one burn job or the latest durable burn jobs.",
        )
        burn_status_parser.add_argument(
            "job",
            nargs="?",
            default="",
            help="Burn job numeric id or UUID.",
        )
        burn_status_parser.add_argument(
            "--limit",
            type=int,
            default=10,
            help="Maximum number of jobs to show when no job id is provided.",
        )
        burn_status_parser.add_argument(
            "--log",
            action="store_true",
            help="Include the persisted job log.",
        )
        burn_work_parser = burn_subparsers.add_parser(
            "work",
            help="Run queued burn jobs; used by the systemd service worker.",
        )
        burn_work_parser.add_argument(
            "--loop",
            action="store_true",
            help="Keep polling for queued jobs instead of running at most one.",
        )
        burn_work_parser.add_argument(
            "--interval",
            type=float,
            default=15.0,
            help="Polling interval in seconds for --loop.",
        )

        serve_parser = subparsers.add_parser(
            "serve",
            help="Serve an existing image artifact over HTTP and print its deployment URL.",
        )
        serve_parser.add_argument(
            "--artifact", default="", help="Registered artifact name to serve."
        )
        serve_parser.add_argument(
            "--image-path",
            default="",
            help="Direct local path to an image file to serve (alternative to --artifact).",
        )
        serve_parser.add_argument(
            "--host", default="0.0.0.0", help="Interface to bind for serving."
        )
        serve_parser.add_argument(
            "--port", type=int, default=8088, help="TCP port to bind for serving."
        )
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
        access_parser.add_argument(
            "--host", required=True, help="RPi hostname or IP address."
        )
        access_parser.add_argument(
            "--ssh-user",
            default=DEFAULT_RECOVERY_SSH_USER,
            help=f"Recovery SSH username to test (default: {DEFAULT_RECOVERY_SSH_USER}).",
        )
        access_parser.add_argument(
            "--ssh-port", type=int, default=22, help="SSH port to test."
        )
        access_parser.add_argument(
            "--ssh-key", default="", help="Private key path for SSH auth testing."
        )
        access_parser.add_argument(
            "--http-url",
            default="",
            help="Suite URL to test. Defaults to http://HOST:8888/ when HTTP checks are enabled.",
        )
        access_parser.add_argument(
            "--http-port", type=int, default=8888, help="Default suite HTTP port."
        )
        access_parser.add_argument(
            "--timeout", type=float, default=5.0, help="Per-check timeout in seconds."
        )
        access_parser.add_argument(
            "--skip-ssh", action="store_true", help="Skip SSH TCP/auth checks."
        )
        access_parser.add_argument(
            "--skip-http",
            action="store_true",
            help="Skip HTTP suite reachability check.",
        )

        watch_parser = subparsers.add_parser(
            "watch-reservations",
            help="Watch reserved image nodes on wlanX/eth0 and report peers awaiting signed registration.",
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
        watch_parser.add_argument(
            "--timeout", type=float, default=1.5, help="Per-probe timeout in seconds."
        )
        watch_parser.add_argument(
            "--interval", type=float, default=30.0, help="Loop interval in seconds."
        )
        watch_parser.add_argument(
            "--once", action="store_true", help="Run one watch pass and exit."
        )

    def handle(self, *args, **options) -> None:
        """Dispatch command to selected action."""

        action = options["action"]
        if action == "build":
            self._handle_build(options)
            return
        if action == "initial-profile":
            self._handle_initial_profile(options)
            return
        if action == "gway-burn":
            self._handle_gway_burn(options)
            return
        if action == "list":
            self._handle_list()
            return
        if action == "register-connect-release":
            self._handle_register_connect_release(options)
            return
        if action == "devices":
            self._handle_devices()
            return
        if action == "write":
            self._handle_write(options)
            return
        if action == "burn":
            self._handle_burn(options)
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
        try:
            storage_options = json.loads(str(options["storage_options"]))
        except json.JSONDecodeError as exc:
            raise CommandError("--storage-options must be valid JSON.") from exc
        if not isinstance(storage_options, dict):
            raise CommandError("--storage-options must decode to a JSON object.")

        customize = not options["skip_customize"]
        recovery_authorized_keys, recovery_ssh_user, skip_recovery_ssh = (
            self._resolve_recovery_ssh_options(
                options,
                require_keys_when_customizing=customize,
            )
        )
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
        minimum_image_size_bytes = self._resolve_minimum_image_size_bytes(options)
        connect_bootstrap_enabled = bool(options["enable_connect_bootstrap"]) or (
            reserve_node and not bool(options["skip_connect_bootstrap"])
        )

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
                connect_bootstrap_enabled=connect_bootstrap_enabled,
                skip_connect_bootstrap=bool(options["skip_connect_bootstrap"]),
                suite_source_path=(
                    Path(str(options["suite_source"]))
                    if str(options["suite_source"]).strip()
                    else None
                ),
                initial_profile_path=(
                    Path(str(options["initial_profile"]))
                    if str(options["initial_profile"]).strip()
                    else None
                ),
                connect_auth_key_path=(
                    Path(str(options["connect_auth_key_file"]))
                    if str(options["connect_auth_key_file"]).strip()
                    else None
                ),
                copy_all_host_networks=bool(options["copy_all_host_networks"]),
                host_network_names=[
                    str(name)
                    for name in options.get("copy_host_network", [])
                    if str(name).strip()
                ],
                host_network_profile_dir=(
                    Path(str(options["host_network_profile_dir"]))
                    if str(options["host_network_profile_dir"]).strip()
                    else None
                ),
                copy_parent_networks=copy_parent_networks,
                reserve_node=reserve_node,
                reserve_hostname_prefix=str(options["reserve_prefix"]),
                reserve_number=reserve_number,
                reserve_role=str(options["reserve_role"]),
                next_number_base_url=str(options["next_number_base_url"]),
                downstream_registration_base_url=str(
                    options["downstream_registration_base_url"]
                ),
                minimum_image_size_bytes=minimum_image_size_bytes,
                storage_backend=str(options["storage_backend"]),
                storage_options=storage_options,
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

    def _handle_initial_profile(self, options: dict[str, object]) -> None:
        """Validate a profile, or explicitly reconcile it during first boot."""

        try:
            profile_path = Path(options["profile"])
            if not options["apply"]:
                profile = load_initial_profile(profile_path)
                self.stdout.write(
                    "initial_profile "
                    "valid=1 mode=check "
                    f"rfids={len(profile.rfids)} "
                    f"charger_configured={int(profile.charger is not None)} "
                    f"auto_start_configured={int(bool(profile.auto_start_id_tag))} "
                    f"redirect_configured={int(profile.redirect is not None)}"
                )
                return
            result = reconcile_initial_profile(profile_path)
        except (InitialProfileError, CommandError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            "initial_profile "
            f"rfids_created={result.rfids_created} "
            f"rfids_existing={result.rfids_existing} "
            f"chargers_created={result.chargers_created} "
            f"chargers_existing={result.chargers_existing} "
            f"auto_start_account_created={int(result.auto_start_account_created)} "
            f"fallback_account_created={int(result.fallback_account_created)} "
            f"fallback_cards_bound={result.fallback_cards_bound} "
            f"redirect_applied={int(result.redirect_applied)}"
        )

    def _handle_gway_burn(self, options: dict[str, object]) -> None:
        """Build a reserved GWAY image and optionally queue the burner job."""

        base_image_uri = self._resolve_gway_burn_base_image_uri(options)
        upstream_base_url = (
            str(options["next_number_base_url"]).strip()
            or default_gway_next_number_base_url()
        )
        downstream_registration_base_url = (
            str(options["downstream_registration_base_url"]).strip()
            or default_gway_downstream_registration_base_url()
        )
        recovery_authorized_keys, recovery_ssh_user, skip_recovery_ssh = (
            self._resolve_recovery_ssh_options(
                options,
                require_keys_when_customizing=True,
            )
        )
        minimum_image_size_bytes = self._resolve_minimum_image_size_bytes(options)
        self._validate_gway_burn_base_image(
            base_image_uri,
            minimum_image_size_bytes=minimum_image_size_bytes,
            output_dir=Path(str(options["output_dir"])),
        )
        resolved_number, reservation_claim_token = self._resolve_gway_burn_reservation(
            options,
            upstream_base_url=upstream_base_url,
        )
        artifact_name = self._gway_burn_artifact_name(options, resolved_number)

        result = self._build_gway_burn_image(
            options,
            artifact_name=artifact_name,
            base_image_uri=base_image_uri,
            recovery_ssh_user=recovery_ssh_user,
            recovery_authorized_keys=recovery_authorized_keys,
            skip_recovery_ssh=skip_recovery_ssh,
            resolved_number=resolved_number,
            downstream_registration_base_url=downstream_registration_base_url,
            reservation_claim_token=reservation_claim_token,
            minimum_image_size_bytes=minimum_image_size_bytes,
        )
        self._write_gway_burn_result(result, resolved_number)
        self._queue_gway_burn_if_configured(result, options)

    def _resolve_gway_burn_base_image_uri(self, options: dict[str, object]) -> str:
        base_image_uri = (
            str(options["base_image_uri"]).strip()
            or os.environ.get("IMAGER_GWAY_BASE_IMAGE_URI", "").strip()
        )
        if not base_image_uri:
            raise CommandError(
                "GWAY image burns require --base-image-uri or IMAGER_GWAY_BASE_IMAGE_URI."
            )
        return base_image_uri

    def _validate_gway_burn_base_image(
        self,
        base_image_uri: str,
        *,
        minimum_image_size_bytes: int | None,
        output_dir: Path,
    ) -> None:
        try:
            effective_minimum_size_bytes = minimum_image_size_bytes
            if effective_minimum_size_bytes is None:
                effective_minimum_size_bytes = (
                    DEFAULT_CUSTOMIZED_IMAGE_MINIMUM_SIZE_GIB * IMAGE_SIZE_BYTES_PER_GIB
                )
            output_dir.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(dir=output_dir) as temporary_directory:
                source_path = _resolve_base_image(
                    base_image_uri, Path(temporary_directory)
                )
                preflight_path = Path(temporary_directory) / source_path.name
                if source_path.resolve() != preflight_path.resolve():
                    shutil.copyfile(source_path, preflight_path)
                else:
                    preflight_path = source_path
                _ensure_image_minimum_size(
                    preflight_path,
                    minimum_size_bytes=effective_minimum_size_bytes,
                )
        except ImagerBuildError as exc:
            raise CommandError(str(exc)) from exc
        except OSError as exc:
            raise CommandError(f"Could not preflight base image: {exc}") from exc

    def _resolve_gway_burn_reservation(
        self,
        options: dict[str, object],
        *,
        upstream_base_url: str,
    ) -> tuple[int, str]:
        reserve_number = options.get("reserve_number")
        if reserve_number is not None:
            resolved_number = int(reserve_number)
            if resolved_number <= 0:
                raise CommandError("--reserve-number must be greater than zero.")
            return resolved_number, ""
        try:
            remote_reservation = next_reservation(
                GWAY_HOSTNAME_PREFIX,
                remote_base_url=upstream_base_url,
            )
        except RemoteReservationError as exc:
            raise CommandError(str(exc)) from exc
        return remote_reservation.number, remote_reservation.claim_token

    def _gway_burn_artifact_name(
        self, options: dict[str, object], resolved_number: int
    ) -> str:
        return (
            str(options["name"]).strip()
            or f"{GWAY_HOSTNAME_PREFIX}-{resolved_number:03d}"
        )

    def _build_gway_burn_image(
        self,
        options: dict[str, object],
        *,
        artifact_name: str,
        base_image_uri: str,
        recovery_ssh_user: str,
        recovery_authorized_keys: list[str],
        skip_recovery_ssh: bool,
        resolved_number: int,
        downstream_registration_base_url: str,
        reservation_claim_token: str,
        minimum_image_size_bytes: int | None,
    ):
        try:
            return build_rpi4b_image(
                name=artifact_name,
                base_image_uri=base_image_uri,
                output_dir=Path(str(options["output_dir"])),
                download_base_uri=str(options["download_base_uri"]),
                git_url=str(options["git_url"]),
                customize=True,
                recovery_ssh_user=recovery_ssh_user,
                recovery_authorized_keys=recovery_authorized_keys,
                skip_recovery_ssh=skip_recovery_ssh,
                reserve_node=True,
                reserve_hostname_prefix=GWAY_HOSTNAME_PREFIX,
                reserve_number=resolved_number,
                reserve_role=str(options["reserve_role"]),
                next_number_base_url="",
                downstream_registration_base_url=downstream_registration_base_url,
                reservation_claim_token=reservation_claim_token,
                connect_bootstrap_enabled=not bool(options["skip_connect_bootstrap"]),
                skip_connect_bootstrap=bool(options["skip_connect_bootstrap"]),
                connect_auth_key_path=(
                    Path(str(options["connect_auth_key_file"]))
                    if str(options["connect_auth_key_file"]).strip()
                    else None
                ),
                minimum_image_size_bytes=minimum_image_size_bytes,
                suite_source_path=(
                    Path(str(options["suite_source"]))
                    if str(options["suite_source"]).strip()
                    else None
                ),
            )
        except ImagerBuildError as exc:
            raise CommandError(str(exc)) from exc

    def _write_gway_burn_result(self, result, resolved_number: int) -> None:
        self.stdout.write(self.style.SUCCESS(f"Built GWAY image: {result.output_path}"))
        self.stdout.write(f"gway_number={resolved_number}")
        self.stdout.write(f"gway_hostname={GWAY_HOSTNAME_PREFIX}-{resolved_number:03d}")
        self.stdout.write(f"sha256={result.sha256}")
        if result.download_uri:
            self.stdout.write(f"download_uri={result.download_uri}")
        if result.reservation:
            self.stdout.write(
                "reserved_node="
                f"{result.reservation.get('hostname')} "
                f"address={result.reservation.get('ipv4_address') or '(none)'} "
                f"id={result.reservation.get('node_id')}"
            )

    def _resolve_gway_burn_device(self, options: dict[str, object]) -> str:
        return (
            str(options["device"]).strip()
            or os.environ.get("IMAGER_GWAY_BURN_DEVICE", "").strip()
            or os.environ.get("IMAGER_BURN_DEVICE", "").strip()
        )

    def _queue_gway_burn_if_configured(
        self, result, options: dict[str, object]
    ) -> None:
        device_path = self._resolve_gway_burn_device(options)
        if not device_path:
            self.stdout.write("burn_job=not queued (no burner device configured)")
            return
        try:
            job = queue_burn_job(
                artifact_name=result.name,
                device_path=device_path,
                backup=bool(options["backup"]),
                backup_dir=str(options["backup_dir"]),
            )
        except ImagerBuildError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"Queued GWAY burn job: {job.uuid}"))
        self.stdout.write(format_job_status(job))

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
            raise CommandError(
                "Recovery authorized key inputs did not contain any usable public keys."
            )
        return keys

    def _resolve_recovery_ssh_options(
        self,
        options: dict[str, object],
        *,
        require_keys_when_customizing: bool,
    ) -> tuple[list[str], str, bool]:
        """Resolve recovery SSH CLI options and fail fast on unsafe mixes."""

        recovery_authorized_keys = self._read_recovery_authorized_keys(
            file_paths=[
                str(path) for path in options.get("recovery_authorized_key_file", [])
            ],
            inline_keys=[
                str(key) for key in options.get("recovery_authorized_key", [])
            ],
        )
        skip_recovery_ssh = bool(options["skip_recovery_ssh"])
        recovery_ssh_user = str(options["recovery_ssh_user"]).strip()
        if skip_recovery_ssh and (recovery_authorized_keys or recovery_ssh_user):
            raise CommandError(
                "--skip-recovery-ssh cannot be combined with recovery SSH key options or --recovery-ssh-user."
            )
        if (
            require_keys_when_customizing
            and not skip_recovery_ssh
            and not recovery_authorized_keys
        ):
            raise CommandError(
                "Recovery SSH is required for customized image builds. "
                "Provide --recovery-authorized-key-file/--recovery-authorized-key or pass --skip-recovery-ssh to opt out."
            )
        if recovery_authorized_keys:
            recovery_ssh_user = recovery_ssh_user or DEFAULT_RECOVERY_SSH_USER
        return recovery_authorized_keys, recovery_ssh_user, skip_recovery_ssh

    def _resolve_minimum_image_size_bytes(
        self, options: dict[str, object]
    ) -> int | None:
        """Return a validated minimum image size in bytes."""

        minimum_image_size_gib = options.get("minimum_image_size_gib")
        if minimum_image_size_gib is None:
            return None
        if int(minimum_image_size_gib) < 0:
            raise CommandError(
                "--minimum-image-size-gib must be greater than or equal to zero."
            )
        return int(minimum_image_size_gib) * IMAGE_SIZE_BYTES_PER_GIB

    def _append_recovery_key_line(
        self, *, keys: list[str], source: str, line: str
    ) -> None:
        """Normalize and append a single recovery authorized-key line when valid."""

        try:
            normalized = normalize_recovery_authorized_key_line(line)
        except RecoveryAuthorizedKeyError as exc:
            self.stderr.write(self.style.WARNING(f"Skipping {exc} from {source}."))
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

    def _handle_register_connect_release(self, options: dict[str, object]) -> None:
        """Create or update a Raspberry Connect image release from an imager artifact."""

        from apps.rpiconnect.models import ConnectImageRelease

        artifact_name = str(options["artifact"]).strip()
        artifact = RaspberryPiImageArtifact.objects.filter(name=artifact_name).first()
        if artifact is None:
            raise CommandError(f"Unknown image artifact: {artifact_name}")
        if artifact.build_profile != "connect-ota":
            raise CommandError(
                "Raspberry Connect update releases require a connect-ota imager artifact."
            )

        profile_manifest = self._connect_release_profile_manifest(artifact)
        version = (
            str(options.get("version") or "").strip()
            or str(profile_manifest.get("release_version") or "").strip()
        )
        if not version:
            raise CommandError(
                "--version is required when the artifact profile manifest has no release_version."
            )

        artifact_url = (
            str(options.get("artifact_url") or "").strip() or artifact.download_uri
        )
        if not artifact_url:
            raise CommandError(
                "Connect image releases require --artifact-url or an artifact download_uri."
            )

        release_name = str(options.get("name") or "").strip() or artifact.name
        retention_days = int(options["retention_days"])
        if retention_days <= 0:
            raise CommandError("--retention-days must be greater than zero.")

        compatibility_tags = self._connect_release_compatibility_tags(
            artifact=artifact,
            profile_manifest=profile_manifest,
            extra_tags=self._split_cli_values(options.get("compatibility_tag", [])),
        )
        build_metadata = {
            "source": "imager",
            "imager_artifact_id": artifact.pk,
            "imager_artifact_name": artifact.name,
            "imager_target": artifact.target,
            "build_engine": artifact.build_engine,
            "build_profile": artifact.build_profile,
            "profile_manifest": profile_manifest,
            "universal_update": True,
            "supported_roles": list(UNIVERSAL_CONNECT_UPDATE_ROLES),
            "device_configuration_policy": (
                "preserve target node role, enabled-app lock, hardware choices, "
                "and local configuration during remote update"
            ),
            "publication": {
                "artifact_url": artifact_url,
                "retention_days": retention_days,
                "verification_command": (
                    f"manage.py imager register-connect-release --artifact {artifact.name} "
                    f"--version {version} --artifact-url {artifact_url}"
                ),
            },
        }

        release, created = ConnectImageRelease.objects.update_or_create(
            name=release_name,
            version=version,
            defaults={
                "artifact_url": artifact_url,
                "checksum": artifact.sha256,
                "compatibility_tags": compatibility_tags,
                "build_metadata": build_metadata,
                "released_at": timezone.now(),
            },
        )

        action = "created" if created else "updated"
        self.stdout.write(
            self.style.SUCCESS(f"Connect image release {action}: {release}")
        )
        self.stdout.write(f"release_id={release.pk}")
        self.stdout.write(f"artifact_url={release.artifact_url}")
        self.stdout.write(f"checksum={release.checksum}")
        self.stdout.write(f"retention_days={retention_days}")
        self.stdout.write(
            "verification_command="
            f"manage.py imager register-connect-release --artifact {artifact.name} "
            f"--version {version} --artifact-url {artifact_url}"
        )

    def _connect_release_profile_manifest(
        self, artifact: RaspberryPiImageArtifact
    ) -> dict[str, object]:
        metadata = artifact.metadata if isinstance(artifact.metadata, dict) else {}
        profile_manifest = metadata.get("profile_manifest")
        if not isinstance(profile_manifest, dict):
            raise CommandError(
                "connect-ota artifacts must include profile_manifest metadata."
            )
        required_artifacts = profile_manifest.get("required_artifacts")
        required_artifact_set = (
            set(required_artifacts)
            if isinstance(required_artifacts, (list, tuple))
            else set()
        )
        missing_artifacts = [
            required
            for required in UNIVERSAL_CONNECT_UPDATE_REQUIRED_ARTIFACTS
            if required not in required_artifact_set
        ]
        if missing_artifacts:
            raise CommandError(
                "connect-ota artifact manifest is missing required artifact(s): "
                + ", ".join(missing_artifacts)
            )
        return dict(profile_manifest)

    def _connect_release_compatibility_tags(
        self,
        *,
        artifact: RaspberryPiImageArtifact,
        profile_manifest: dict[str, object],
        extra_tags: tuple[str, ...],
    ) -> list[str]:
        tags = [
            "universal-connect-update",
            artifact.target,
            artifact.build_profile,
            *[f"role:{role.lower()}" for role in UNIVERSAL_CONNECT_UPDATE_ROLES],
        ]
        for key in (
            "base_os",
            "architecture",
            "compatibility_model",
            "compatibility_board",
            "ota_channel",
            "ota_artifact_type",
        ):
            value = str(profile_manifest.get(key) or "").strip()
            if value:
                tags.append(value)
        tags.extend(extra_tags)
        return list(dict.fromkeys(tag for tag in tags if tag))

    def _split_cli_values(self, values: object) -> tuple[str, ...]:
        split_values: list[str] = []
        for value in values if isinstance(values, list) else []:
            split_values.extend(
                part.strip()
                for part in str(value).replace(",", " ").replace(";", " ").split()
                if part.strip()
            )
        return tuple(dict.fromkeys(split_values))

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
            mountpoints = (
                ",".join(device.mountpoints) if device.mountpoints else "(none)"
            )
            partitions = ",".join(device.partitions) if device.partitions else "(none)"
            identity_paths = (
                ",".join(device.identity_paths) if device.identity_paths else "(none)"
            )
            write_blocked = device.write_blocked_reason or "(none)"
            self.stdout.write(
                f"{device.path} size={device.size_bytes} transport={device.transport or '(unknown)'} "
                f"removable={'yes' if device.removable else 'no'} protected={'yes' if device.protected else 'no'} "
                f"vendor={device.vendor or '(unknown)'} model={device.model or '(unknown)'} "
                f"serial={device.serial or '(unknown)'} write_blocked={write_blocked} "
                f"identity_paths={identity_paths} partitions={partitions} mounts={mountpoints}"
            )

    def _handle_write(self, options: dict[str, object]) -> None:
        """Write image artifact to block device with safety checks and verification."""

        artifact_name = str(options["artifact"])
        image_path = str(options["image_path"])
        if bool(artifact_name) == bool(image_path):
            raise CommandError("Provide exactly one of --artifact or --image-path.")

        quiet_usb_kwargs = {"log": self.stdout.write}
        if bool(options["no_quiet_usb"]):
            quiet_usb_kwargs["enabled"] = False
        write_kwargs = {
            "device_path": str(options["device"]),
            "artifact_name": artifact_name,
            "image_path": image_path,
            "confirmed": bool(options["yes"]),
            "backup": bool(options["backup"]),
            "backup_dir": (
                Path(str(options["backup_dir"]))
                if str(options["backup_dir"]).strip()
                else None
            ),
        }
        if bool(options["no_windows_automount_guard"]):
            write_kwargs["windows_automount_guard"] = False

        try:
            with quiet_usb_pollers(**quiet_usb_kwargs):
                result = write_image_to_device(**write_kwargs)
        except ImagerBuildError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(f"Wrote {result.image_path} -> {result.device_path}")
        )
        self.stdout.write(f"size_bytes={result.size_bytes}")
        self.stdout.write(f"source_sha256={result.source_sha256}")
        self.stdout.write(f"written_sha256={result.written_sha256}")
        self.stdout.write(f"verified={'yes' if result.verified else 'no'}")
        if result.backup is not None:
            self.stdout.write(f"backup_path={result.backup.path}")
            self.stdout.write(f"backup_size_bytes={result.backup.size_bytes}")
            self.stdout.write(f"backup_sha256={result.backup.sha256}")
            self.stdout.write(
                f"backup_verified={'yes' if result.backup.verified else 'no'}"
            )

    def _handle_burn(self, options: dict[str, object]) -> None:
        """Dispatch durable burn queue commands."""

        burn_action = str(options["burn_action"])
        if burn_action == "queue":
            self._handle_burn_queue(options)
            return
        if burn_action == "status":
            self._handle_burn_status(options)
            return
        if burn_action == "work":
            self._handle_burn_work(options)
            return
        raise CommandError(f"Unsupported burn action '{burn_action}'.")

    def _handle_burn_queue(self, options: dict[str, object]) -> None:
        """Create a durable burn job after source and target preflight."""

        artifact_name = str(options["artifact"])
        image_path = str(options["image_path"])
        if bool(artifact_name) == bool(image_path):
            raise CommandError("Provide exactly one of --artifact or --image.")
        try:
            job = queue_burn_job(
                artifact_name=artifact_name,
                image_path=image_path,
                device_path=str(options["device"]),
                backup=bool(options["backup"]),
                backup_dir=str(options["backup_dir"]),
            )
        except ImagerBuildError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(f"Queued burn job: {job.uuid}"))
        self.stdout.write(format_job_status(job))

    def _handle_burn_status(self, options: dict[str, object]) -> None:
        """Print durable burn job state and optional logs."""

        identifier = str(options["job"]).strip()
        include_log = bool(options["log"])
        if identifier:
            job = self._get_burn_job(identifier)
            self.stdout.write(format_job_status(job))
            self.stdout.write(f"created_at={job.created_at.isoformat()}")
            if job.started_at:
                self.stdout.write(f"started_at={job.started_at.isoformat()}")
            if job.finished_at:
                self.stdout.write(f"finished_at={job.finished_at.isoformat()}")
            if job.result:
                self.stdout.write(f"result={json.dumps(job.result, sort_keys=True)}")
            if include_log and job.log:
                self.stdout.write("log:")
                self.stdout.write(job.log.rstrip())
            return

        limit = int(options["limit"])
        if limit <= 0:
            raise CommandError("--limit must be greater than zero.")
        jobs = RaspberryPiImageBurnJob.objects.order_by("-created_at", "-pk")[:limit]
        if not jobs:
            self.stdout.write("No burn jobs are registered.")
            return
        for job in jobs:
            self.stdout.write(format_job_status(job))

    def _handle_burn_work(self, options: dict[str, object]) -> None:
        """Run durable burn queue worker logic."""

        interval = float(options["interval"])
        if interval <= 0:
            raise CommandError("--interval must be greater than zero.")
        if options["loop"]:
            self.stdout.write("Starting durable burn worker loop.")
            work_loop(interval=interval)
            return
        job = work_once()
        if job is None:
            self.stdout.write("No queued burn jobs.")
            return
        self.stdout.write(format_job_status(job))

    def _get_burn_job(self, identifier: str) -> RaspberryPiImageBurnJob:
        """Resolve a burn job from a numeric id or UUID."""

        if identifier.isdigit():
            job = RaspberryPiImageBurnJob.objects.filter(pk=int(identifier)).first()
            if job is not None:
                return job
        try:
            job = RaspberryPiImageBurnJob.objects.filter(uuid=identifier).first()
        except (ValidationError, ValueError):
            job = None
        if job is not None:
            return job
        raise CommandError(f"Burn job '{identifier}' was not found.")

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
            serve_image_file(
                image_path=result.image_path, host=result.host, port=result.port
            )
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
        self.stdout.write(
            self.style.SUCCESS(f"RPi access test passed for {result.host}.")
        )

    def _handle_watch_reservations(self, options: dict[str, object]) -> None:
        """Watch reserved nodes and report peers awaiting signed registration."""

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
