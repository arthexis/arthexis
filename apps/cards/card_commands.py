from __future__ import annotations

import logging
import multiprocessing
import queue
import socket
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from io import StringIO
from typing import Any

from django.core.management import call_command
from django.db import close_old_connections
from django.utils import timezone

from apps.cards.command_layout import (
    COMMAND_LIFECYCLE_TRIGGERED,
    CardLayoutError,
    DecodedCommandCard,
    command_payload_digest_for_card,
    decode_command_card_from_dump,
    lifecycle_mode_from_flags,
    normalize_command_lifecycle_mode,
    result_digest,
)
from apps.cards.models import RFID, RFIDCommandExecution, RFIDCommandTemplate
from utils.command_api import (
    COMMAND_ALIASES,
    SUPPORTED_OPERATIONAL_COMMANDS,
    _resolve_runtime_sigils,
    _translate_namespaced_arguments,
    normalize_command_name,
)

logger = logging.getLogger(__name__)

MAX_COMMAND_CAPTURE_CHARS = 4000
DEFAULT_SUITE_COMMAND_TIMEOUT = 120
MAX_SUITE_COMMAND_TIMEOUT = 3600
RUN_SUITE_COMMAND_PERMISSION = "cards.run_suite_command_card"


class SuiteCommandTimeout(RuntimeError):
    """Raised when a suite command exceeds its configured card timeout."""


@dataclass(slots=True)
class RFIDCommandResult:
    ok: bool
    summary: str = ""
    payload: dict[str, Any] | None = None

    def as_payload(self) -> dict[str, Any]:
        result = {
            "ok": self.ok,
            "summary": self.summary[:160],
        }
        if self.payload:
            result["payload"] = self.payload
        return result


@dataclass(frozen=True, slots=True)
class RFIDCommandDefinition:
    name: str
    handler: Callable[..., RFIDCommandResult]
    permission: str = ""
    requires_user: bool = False


def _command_noop(
    *, card: DecodedCommandCard, execution: RFIDCommandExecution, user
) -> RFIDCommandResult:
    return RFIDCommandResult(
        ok=True,
        summary=f"{card.name or execution.rfid_value}: no operation",
        payload={"command": card.command, "params": card.params},
    )


def _command_log(
    *, card: DecodedCommandCard, execution: RFIDCommandExecution, user
) -> RFIDCommandResult:
    username = user.get_username() if user is not None else ""
    logger.info(
        "RFID command card LOG execution=%s card=%s user=%s params=%s",
        execution.execution_id,
        card.name,
        username,
        card.params,
    )
    return RFIDCommandResult(
        ok=True,
        summary=f"{card.name or execution.rfid_value}: logged",
        payload={"user": username, "params": card.params},
    )


def _command_reject(
    *, card: DecodedCommandCard, execution: RFIDCommandExecution, user
) -> RFIDCommandResult:
    return RFIDCommandResult(
        ok=False,
        summary=f"{card.name or execution.rfid_value}: rejected",
        payload={"reason": "command requested rejection"},
    )


def _clean_command_args(value: object) -> list[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ValueError("params.args must be a list")
    return [str(item) for item in value]


def _tail_command_output(value: object) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value)
    return text[-MAX_COMMAND_CAPTURE_CHARS:]


def _clean_suite_command_timeout(value: object) -> float:
    try:
        timeout = float(value or DEFAULT_SUITE_COMMAND_TIMEOUT)
    except (TypeError, ValueError):
        timeout = DEFAULT_SUITE_COMMAND_TIMEOUT
    return max(1.0, min(timeout, float(MAX_SUITE_COMMAND_TIMEOUT)))


def _call_suite_management_command_direct(
    django_command: str,
    translated_args: tuple[str, ...],
) -> tuple[int, str, str]:
    stdout_buffer = StringIO()
    stderr_buffer = StringIO()
    with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
        call_command(
            django_command,
            *translated_args,
            stdout=stdout_buffer,
            stderr=stderr_buffer,
        )
    return 0, stdout_buffer.getvalue(), stderr_buffer.getvalue()


def _call_suite_management_command_worker(
    django_command: str,
    translated_args: tuple[str, ...],
    result_queue,
) -> None:
    close_old_connections()
    try:
        returncode, stdout, stderr = _call_suite_management_command_direct(
            django_command,
            translated_args,
        )
        result_queue.put(
            {
                "returncode": returncode,
                "stdout": _tail_command_output(stdout),
                "stderr": _tail_command_output(stderr),
            }
        )
    except Exception as exc:
        result_queue.put({"error": str(exc), "error_type": type(exc).__name__})
    finally:
        close_old_connections()


def _call_suite_management_command(
    django_command: str,
    translated_args: tuple[str, ...],
    *,
    timeout: float,
) -> tuple[int, str, str]:
    context = multiprocessing.get_context()
    result_queue = context.Queue()
    process = context.Process(
        target=_call_suite_management_command_worker,
        args=(django_command, translated_args, result_queue),
    )
    process.start()
    process.join(timeout)
    if process.is_alive():
        process.terminate()
        process.join(1)
        if process.is_alive():  # pragma: no cover - platform fallback
            process.kill()
            process.join(1)
        raise SuiteCommandTimeout
    try:
        result = result_queue.get(timeout=1)
    except queue.Empty:
        exitcode = process.exitcode if process.exitcode is not None else 1
        return exitcode, "", f"command process exited {exitcode}"
    if result.get("error"):
        raise RuntimeError(str(result["error"]))
    return (
        int(result.get("returncode") or 0),
        str(result.get("stdout") or ""),
        str(result.get("stderr") or ""),
    )


def _command_suite_command(
    *, card: DecodedCommandCard, execution: RFIDCommandExecution, user
) -> RFIDCommandResult:
    params = card.params or {}
    raw_command = str(params.get("command") or "").strip()
    if not raw_command:
        return RFIDCommandResult(ok=False, summary="Suite command is missing")
    try:
        command = normalize_command_name(raw_command)
    except ValueError as exc:
        return RFIDCommandResult(ok=False, summary=str(exc))
    if command not in SUPPORTED_OPERATIONAL_COMMANDS:
        return RFIDCommandResult(
            ok=False,
            summary=f"Unsupported suite command: {raw_command}",
            payload={"command": raw_command},
        )
    try:
        args = _clean_command_args(params.get("args"))
    except ValueError as exc:
        return RFIDCommandResult(ok=False, summary=str(exc))
    timeout = _clean_suite_command_timeout(params.get("timeout"))
    django_command = COMMAND_ALIASES.get(command, command)
    translated_args = _resolve_runtime_sigils(
        _translate_namespaced_arguments(command, args)
    )
    try:
        returncode, stdout, stderr = _call_suite_management_command(
            django_command,
            tuple(translated_args),
            timeout=timeout,
        )
    except SuiteCommandTimeout:
        return RFIDCommandResult(
            ok=False,
            summary=f"{command} timed out",
            payload={
                "command": command,
                "args": list(translated_args),
                "timeout": timeout,
            },
        )
    except Exception as exc:
        return RFIDCommandResult(
            ok=False,
            summary=f"Failed to execute {command}: {exc}",
            payload={
                "command": command,
                "args": list(translated_args),
                "error": str(exc),
            },
        )
    stdout = _tail_command_output(stdout)
    stderr = _tail_command_output(stderr)
    output_line = next(
        (line.strip() for line in stdout.splitlines() if line.strip()),
        "",
    )
    summary = output_line or f"{command} exited {returncode}"
    return RFIDCommandResult(
        ok=returncode == 0,
        summary=summary,
        payload={
            "command": command,
            "args": list(translated_args),
            "returncode": returncode,
            "stdout": stdout,
            "stderr": stderr,
        },
    )


COMMANDS: dict[str, RFIDCommandDefinition] = {
    "LOG": RFIDCommandDefinition("LOG", _command_log),
    "NOOP": RFIDCommandDefinition("NOOP", _command_noop),
    "REJECT": RFIDCommandDefinition("REJECT", _command_reject),
    "SUITE_COMMAND": RFIDCommandDefinition(
        "SUITE_COMMAND",
        _command_suite_command,
        permission=RUN_SUITE_COMMAND_PERMISSION,
        requires_user=True,
    ),
}


def command_choices() -> list[tuple[str, str]]:
    return [(name, name.title()) for name in sorted(COMMANDS)]


def default_reader_id() -> str:
    return socket.gethostname() or "unknown-reader"


def _find_tag(scan_payload: dict[str, Any]) -> RFID | None:
    label_id = scan_payload.get("label_id")
    if label_id not in (None, ""):
        try:
            tag = RFID.objects.filter(pk=int(label_id)).first()
        except (TypeError, ValueError):
            tag = None
        if tag is not None:
            return tag
    rfid_value = RFID.normalize_code(str(scan_payload.get("rfid") or ""))
    return RFID.find_match(rfid_value) if rfid_value else None


def _is_recognized(tag: RFID | None, card: DecodedCommandCard) -> bool:
    if tag is None:
        return False
    if not tag.command_card_name or tag.command_card_name != card.name:
        return False
    expected_provenance = (tag.command_provenance_key or "").strip().upper()
    card_provenance = (card.metadata.provenance_key or "").strip().upper()
    if expected_provenance and expected_provenance != card_provenance:
        return False
    return True


def _previous_result_digest(card: DecodedCommandCard) -> str:
    return result_digest(card.result) if card.result else ""


def _authorized_command_payload(tag: RFID | None, card: DecodedCommandCard) -> bool:
    if tag is None:
        return False
    expected_digest = str(tag.command_payload_digest or "").strip().lower()
    if not expected_digest:
        return False
    return command_payload_digest_for_card(card).lower() == expected_digest


def _metadata_lifecycle_mode(metadata: object) -> str:
    if not isinstance(metadata, dict):
        return ""
    lifecycle_mode = metadata.get("lifecycle_mode")
    if lifecycle_mode not in (None, ""):
        try:
            return normalize_command_lifecycle_mode(lifecycle_mode)
        except CardLayoutError:
            return ""
    flags = metadata.get("flags")
    if flags not in (None, ""):
        try:
            return lifecycle_mode_from_flags(int(flags))
        except (TypeError, ValueError):
            return ""
    return ""


def _stored_command_lifecycle_mode(
    tag: RFID | None,
    template: RFIDCommandTemplate | None,
) -> str:
    if tag is not None:
        lifecycle_mode = _metadata_lifecycle_mode(tag.command_card_metadata)
        if lifecycle_mode:
            return lifecycle_mode
    if template is not None:
        try:
            return normalize_command_lifecycle_mode(template.lifecycle_mode)
        except CardLayoutError:
            return ""
    return COMMAND_LIFECYCLE_TRIGGERED


def _authorized_command_lifecycle(
    tag: RFID | None,
    template: RFIDCommandTemplate | None,
    card: DecodedCommandCard,
) -> bool:
    expected_lifecycle = _stored_command_lifecycle_mode(tag, template)
    if not expected_lifecycle:
        return False
    return card.metadata.lifecycle_mode == expected_lifecycle


def _is_allowed_command_tag(tag: RFID | None, scan_payload: dict[str, Any]) -> bool:
    if tag is None or not tag.allowed:
        return False
    allowed_val = scan_payload.get("allowed")
    if allowed_val in (False, "False", "false", "0", 0):
        return False
    return True


def _stable_rfid_value(scan_payload: dict[str, Any], tag: RFID | None) -> str:
    scanned = RFID.normalize_code(str(scan_payload.get("rfid") or ""))
    if scanned:
        return scanned
    if tag is not None:
        return RFID.normalize_code(getattr(tag, "rfid", ""))
    return ""


def _base_execution_kwargs(
    *,
    scan_payload: dict[str, Any],
    card: DecodedCommandCard,
    tag: RFID | None,
    template: RFIDCommandTemplate | None,
    reader_id: str,
) -> dict[str, Any]:
    return {
        "rfid": tag,
        "template": template,
        "rfid_value": _stable_rfid_value(scan_payload, tag),
        "card_name": card.name,
        "card_provenance_key": card.metadata.provenance_key,
        "reader_id": reader_id,
        "command_name": str(card.command or "")[:64],
        "command_params": card.params,
        "command_sigils": card.sigils,
        "command_payload": card.raw_command,
        "card_result_before": card.result,
        "expected_previous_result_digest": (tag.command_result_digest if tag else ""),
        "card_previous_result_digest": _previous_result_digest(card),
    }


def _blocked_execution(
    *,
    scan_payload: dict[str, Any],
    card: DecodedCommandCard,
    tag: RFID | None,
    template: RFIDCommandTemplate | None,
    reader_id: str,
    detail: str,
) -> RFIDCommandExecution:
    execution = RFIDCommandExecution.objects.create(
        **_base_execution_kwargs(
            scan_payload=scan_payload,
            card=card,
            tag=tag,
            template=template,
            reader_id=reader_id,
        ),
        status=RFIDCommandExecution.Status.BLOCKED,
        status_detail=detail,
        completed_at=timezone.now(),
    )
    return execution


def _build_card_result(
    *,
    execution: RFIDCommandExecution,
    status: str,
    ok: bool,
    summary: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = {
        "execution_id": str(execution.execution_id),
        "status": status,
        "ok": ok,
        "command": execution.command_name,
        "triggered_at": execution.triggered_at.isoformat(),
        "reader": execution.reader_id,
        "summary": summary[:160],
    }
    if payload:
        result["payload"] = payload
    return result


def _write_card_result(
    result_writer: Callable[..., dict[str, Any]],
    *,
    result_payload: dict[str, Any],
    command_block_count: int,
    expected_rfid: str,
) -> dict[str, Any]:
    try:
        return result_writer(
            result_payload=result_payload,
            command_block_count=command_block_count,
            expected_rfid=expected_rfid,
        )
    except Exception as exc:  # pragma: no cover - defensive audit guard
        logger.exception("RFID command-card result write failed")
        return {"error": f"{type(exc).__name__}: {exc}"}


def execute_command_card_payload(
    scan_payload: dict[str, Any],
    *,
    reader_id: str | None = None,
    result_writer: Callable[..., dict[str, Any]] | None = None,
) -> RFIDCommandExecution | None:
    """Validate and execute one decoded RFID command-card payload."""

    card = decode_command_card_from_dump(scan_payload.get("dump"))
    if card is None or not card.command:
        return None

    effective_reader_id = (reader_id or default_reader_id())[:64]
    tag = _find_tag(scan_payload)
    discovered_template, _created = RFIDCommandTemplate.discover_from_card(card)
    template = (
        tag.command_template
        if tag is not None and tag.command_template_id
        else discovered_template
    )
    definition = COMMANDS.get(card.command)
    if not _is_recognized(tag, card):
        return _blocked_execution(
            scan_payload=scan_payload,
            card=card,
            tag=tag,
            template=template,
            reader_id=effective_reader_id,
            detail="unrecognized command card",
        )
    if not _authorized_command_payload(tag, card):
        return _blocked_execution(
            scan_payload=scan_payload,
            card=card,
            tag=tag,
            template=template,
            reader_id=effective_reader_id,
            detail="card command payload does not match database",
        )
    if not _authorized_command_lifecycle(tag, template, card):
        return _blocked_execution(
            scan_payload=scan_payload,
            card=card,
            tag=tag,
            template=template,
            reader_id=effective_reader_id,
            detail="card command lifecycle does not match database",
        )
    if not _is_allowed_command_tag(tag, scan_payload):
        return _blocked_execution(
            scan_payload=scan_payload,
            card=card,
            tag=tag,
            template=template,
            reader_id=effective_reader_id,
            detail="command card is not allowed",
        )
    if definition is None:
        return _blocked_execution(
            scan_payload=scan_payload,
            card=card,
            tag=tag,
            template=template,
            reader_id=effective_reader_id,
            detail=f"unknown command: {card.command}",
        )

    stable_rfid = _stable_rfid_value(scan_payload, tag)
    if not stable_rfid:
        return _blocked_execution(
            scan_payload=scan_payload,
            card=card,
            tag=tag,
            template=template,
            reader_id=effective_reader_id,
            detail="command card requires stable rfid",
        )

    expected_digest = tag.command_result_digest if tag else ""
    previous_digest = _previous_result_digest(card)
    if expected_digest and previous_digest != expected_digest:
        return _blocked_execution(
            scan_payload=scan_payload,
            card=card,
            tag=tag,
            template=template,
            reader_id=effective_reader_id,
            detail="card result does not match database",
        )

    run_as_user = tag.command_owner_user() if tag is not None else None
    if definition.requires_user and run_as_user is None:
        return _blocked_execution(
            scan_payload=scan_payload,
            card=card,
            tag=tag,
            template=template,
            reader_id=effective_reader_id,
            detail="command requires an owner user",
        )
    if definition.permission and (
        run_as_user is None or not run_as_user.has_perm(definition.permission)
    ):
        return _blocked_execution(
            scan_payload=scan_payload,
            card=card,
            tag=tag,
            template=template,
            reader_id=effective_reader_id,
            detail=f"missing permission: {definition.permission}",
        )

    execution = RFIDCommandExecution.objects.create(
        **_base_execution_kwargs(
            scan_payload=scan_payload,
            card=card,
            tag=tag,
            template=template,
            reader_id=effective_reader_id,
        ),
        run_as_user=run_as_user,
        status=RFIDCommandExecution.Status.STARTED,
        preflight_ok=True,
    )

    if result_writer is None:
        from apps.cards.reader import write_current_card_command_result

        result_writer = write_current_card_command_result

    started_result = _build_card_result(
        execution=execution,
        status="started",
        ok=True,
        summary="Command accepted",
    )
    started_write = _write_card_result(
        result_writer,
        result_payload=started_result,
        command_block_count=card.command_block_count,
        expected_rfid=execution.rfid_value,
    )
    if started_write.get("error"):
        execution.mark_failed(
            f"failed to write started result: {started_write['error']}",
            result=started_write,
        )
        return execution

    started_digest = str(started_write.get("result_digest") or "")
    execution.card_result_written = started_write.get("result") or started_result
    execution.result_digest = started_digest
    execution.save(update_fields=["card_result_written", "result_digest"])
    if tag is not None and started_digest:
        tag.command_result_digest = started_digest
        tag.save(update_fields=["command_result_digest"])

    try:
        command_result = definition.handler(
            card=card,
            execution=execution,
            user=run_as_user,
        )
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.exception("RFID command handler failed: %s", card.command)
        command_result = RFIDCommandResult(
            ok=False,
            summary=str(exc),
            payload={"error": type(exc).__name__},
        )

    status = "succeeded" if command_result.ok else "failed"
    final_payload = command_result.as_payload()
    final_result = _build_card_result(
        execution=execution,
        status=status,
        ok=command_result.ok,
        summary=final_payload["summary"],
        payload=final_payload.get("payload"),
    )
    final_write = _write_card_result(
        result_writer,
        result_payload=final_result,
        command_block_count=card.command_block_count,
        expected_rfid=execution.rfid_value,
    )
    if final_write.get("error"):
        terminal_digest = result_digest(final_result)
        execution.status = RFIDCommandExecution.Status.FAILED
        execution.status_detail = (
            f"failed to write final result: {final_write['error']}"
        )
        execution.result = final_result
        execution.result_digest = terminal_digest
        execution.completed_at = timezone.now()
        execution.save(
            update_fields=[
                "status",
                "status_detail",
                "result",
                "result_digest",
                "completed_at",
            ]
        )
        if tag is not None and terminal_digest:
            tag.command_result_digest = terminal_digest
            tag.save(update_fields=["command_result_digest"])
        return execution

    final_digest = str(final_write.get("result_digest") or "")
    card_result_written = final_write.get("result") or final_result
    if command_result.ok:
        execution.mark_succeeded(
            result=final_result,
            card_result_written=card_result_written,
            result_digest=final_digest,
        )
    else:
        execution.mark_failed(command_result.summary, result=final_result)
        execution.card_result_written = card_result_written
        execution.result_digest = final_digest
        execution.save(update_fields=["card_result_written", "result_digest"])

    if tag is not None and final_digest:
        tag.command_result_digest = final_digest
        tag.save(update_fields=["command_result_digest"])
    return execution
