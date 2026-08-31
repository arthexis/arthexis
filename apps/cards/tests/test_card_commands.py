from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.cards import card_commands, command_layout
from apps.cards.card_commands import execute_command_card_payload
from apps.cards.models import RFID, RFIDCommandExecution


def _dump_from_blocks(blocks):
    return [{"block": block, "data": data} for block, data in blocks.items()]


COMMAND_CARD_NAME = "Suite Upgrade"
COMMAND_PARAMS = {"channel": "stable"}
COMMAND_PROVENANCE_KEY = "AABBCCDDEEFF0011"


def _command_payload_digest(
    *,
    name=COMMAND_CARD_NAME,
    command="LOG",
    params=None,
    sigils=None,
):
    return command_layout.command_payload_digest(
        name=name,
        command=command,
        params=COMMAND_PARAMS if params is None else params,
        sigils=sigils,
    )


def _command_dump(*, result=None, command="LOG", params=None, sigils=None):
    blocks, metadata = command_layout.build_command_card_blocks(
        name=COMMAND_CARD_NAME,
        command=command,
        params=COMMAND_PARAMS if params is None else params,
        sigils=sigils,
        provenance_key=COMMAND_PROVENANCE_KEY,
    )
    if result:
        encoded, _digest, _stored = command_layout.encode_result_payload(
            result,
            command_block_count=metadata.command_block_count,
        )
        for index, block in enumerate(
            command_layout.result_data_blocks(metadata.command_block_count)
        ):
            blocks[block] = encoded[index * 16 : index * 16 + 16]
    return _dump_from_blocks(blocks), metadata


def test_suite_command_child_uses_default_context_and_blocking_queue(monkeypatch):
    class FakeQueue:
        def __init__(self):
            self.timeout = None

        def get(self, *, timeout):
            self.timeout = timeout
            return {"returncode": 0, "stdout": "ok", "stderr": ""}

    class FakeProcess:
        exitcode = 0

        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start(self):
            pass

        def join(self, timeout=None):
            pass

        def is_alive(self):
            return False

    class FakeContext:
        def Queue(self):
            captured["queue"] = FakeQueue()
            return captured["queue"]

        def Process(self, **kwargs):
            captured["process"] = FakeProcess(**kwargs)
            return captured["process"]

    captured = {}

    def fake_get_context(*args):
        captured["context_args"] = args
        return FakeContext()

    monkeypatch.setattr(card_commands.multiprocessing, "get_context", fake_get_context)

    assert card_commands._call_suite_management_command("health", (), timeout=2) == (
        0,
        "ok",
        "",
    )
    assert captured["context_args"] == ()
    assert captured["queue"].timeout == 1


class RFIDCommandCardExecutionTests(TestCase):
    def test_recognized_card_writes_started_and_final_results(self):
        user = get_user_model().objects.create_user(username="rfid-owner")
        dump, metadata = _command_dump()
        tag = RFID.objects.create(
            rfid="ABCD1234",
            command_card_name="Suite Upgrade",
            command_card_metadata=metadata.as_dict(),
            command_payload_digest=_command_payload_digest(),
            command_provenance_key=metadata.provenance_key,
            owner_user=user,
        )
        writes: list[dict[str, object]] = []

        def writer(**kwargs):
            result_payload = kwargs["result_payload"]
            digest = command_layout.result_digest(result_payload)
            writes.append(dict(kwargs, digest=digest))
            return {"result": result_payload, "result_digest": digest}

        execution = execute_command_card_payload(
            {"rfid": "ABCD1234", "label_id": tag.pk, "dump": dump},
            reader_id="reader-1",
            result_writer=writer,
        )

        assert execution is not None
        execution.refresh_from_db()
        tag.refresh_from_db()
        assert execution.status == RFIDCommandExecution.Status.SUCCEEDED
        assert execution.run_as_user == user
        assert execution.command_name == "LOG"
        assert execution.command_params == {"channel": "stable"}
        assert [write["result_payload"]["status"] for write in writes] == [
            "started",
            "succeeded",
        ]
        assert tag.command_result_digest == writes[-1]["digest"]

    def test_disabled_tag_blocks_execution_without_writing(self):
        dump, metadata = _command_dump()
        tag = RFID.objects.create(
            rfid="ABCD1234",
            allowed=False,
            command_card_name="Suite Upgrade",
            command_card_metadata=metadata.as_dict(),
            command_payload_digest=_command_payload_digest(),
            command_provenance_key=metadata.provenance_key,
        )
        writes: list[dict[str, object]] = []

        execution = execute_command_card_payload(
            {"rfid": "ABCD1234", "label_id": tag.pk, "dump": dump, "allowed": False},
            reader_id="reader-1",
            result_writer=lambda **kwargs: writes.append(kwargs) or {},
        )

        assert execution is not None
        assert execution.status == RFIDCommandExecution.Status.BLOCKED
        assert execution.status_detail == "command card is not allowed"
        assert writes == []

    def test_result_mismatch_blocks_execution_without_writing(self):
        dump, metadata = _command_dump(result={"status": "old", "ok": True})
        tag = RFID.objects.create(
            rfid="ABCD1234",
            command_card_name="Suite Upgrade",
            command_card_metadata=metadata.as_dict(),
            command_payload_digest=_command_payload_digest(),
            command_provenance_key=metadata.provenance_key,
            command_result_digest="0" * 64,
        )
        writes: list[dict[str, object]] = []

        execution = execute_command_card_payload(
            {"rfid": "ABCD1234", "label_id": tag.pk, "dump": dump},
            reader_id="reader-1",
            result_writer=lambda **kwargs: writes.append(kwargs) or {},
        )

        assert execution is not None
        assert execution.status == RFIDCommandExecution.Status.BLOCKED
        assert execution.status_detail == "card result does not match database"
        assert writes == []

    def test_final_result_write_failure_records_expected_digest(self):
        dump, metadata = _command_dump()
        tag = RFID.objects.create(
            rfid="ABCD1234",
            command_card_name="Suite Upgrade",
            command_card_metadata=metadata.as_dict(),
            command_payload_digest=_command_payload_digest(),
            command_provenance_key=metadata.provenance_key,
        )
        writes: list[dict[str, object]] = []

        def writer(**kwargs):
            if not writes:
                result_payload = kwargs["result_payload"]
                result_digest = command_layout.result_digest(result_payload)
                writes.append(dict(kwargs, result_digest=result_digest))
                return {
                    "result": result_payload,
                    "result_digest": result_digest,
                }
            writes.append(kwargs)
            return {"error": "writer offline"}

        execution = execute_command_card_payload(
            {"rfid": "ABCD1234", "label_id": tag.pk, "dump": dump},
            reader_id="reader-1",
            result_writer=writer,
        )

        assert execution is not None
        execution.refresh_from_db()
        tag.refresh_from_db()
        assert execution.status == RFIDCommandExecution.Status.FAILED
        assert execution.status_detail == "failed to write final result: writer offline"
        assert execution.result["status"] == "succeeded"
        assert execution.card_result_written["status"] == "started"
        assert execution.result_digest == command_layout.result_digest(execution.result)
        assert tag.command_result_digest == execution.result_digest
        assert tag.command_result_digest != writes[0]["result_digest"]

        retry_dump, _metadata = _command_dump(result=execution.card_result_written)
        retry = execute_command_card_payload(
            {"rfid": "ABCD1234", "label_id": tag.pk, "dump": retry_dump},
            reader_id="reader-1",
            result_writer=lambda **kwargs: pytest.fail("handler should stay blocked"),
        )

        assert retry is not None
        assert retry.status == RFIDCommandExecution.Status.BLOCKED
        assert retry.status_detail == "card result does not match database"

    def test_label_only_payload_uses_registered_rfid_for_result_writes(self):
        dump, metadata = _command_dump()
        tag = RFID.objects.create(
            rfid="ABCD1234",
            command_card_name="Suite Upgrade",
            command_card_metadata=metadata.as_dict(),
            command_payload_digest=_command_payload_digest(),
            command_provenance_key=metadata.provenance_key,
        )
        expected: list[str] = []

        def writer(**kwargs):
            expected.append(kwargs["expected_rfid"])
            result_payload = kwargs["result_payload"]
            return {
                "result": result_payload,
                "result_digest": command_layout.result_digest(result_payload),
            }

        execution = execute_command_card_payload(
            {"label_id": tag.pk, "dump": dump},
            reader_id="reader-1",
            result_writer=writer,
        )

        assert execution is not None
        assert execution.rfid_value == "ABCD1234"
        assert expected == ["ABCD1234", "ABCD1234"]

    def test_result_writer_exception_marks_execution_failed(self):
        dump, metadata = _command_dump()
        tag = RFID.objects.create(
            rfid="ABCD1234",
            command_card_name="Suite Upgrade",
            command_card_metadata=metadata.as_dict(),
            command_payload_digest=_command_payload_digest(),
            command_provenance_key=metadata.provenance_key,
        )

        def writer(**kwargs):
            raise RuntimeError("writer exploded")

        execution = execute_command_card_payload(
            {"rfid": "ABCD1234", "label_id": tag.pk, "dump": dump},
            reader_id="reader-1",
            result_writer=writer,
        )

        assert execution is not None
        execution.refresh_from_db()
        assert execution.status == RFIDCommandExecution.Status.FAILED
        assert (
            execution.status_detail
            == "failed to write started result: RuntimeError: writer exploded"
        )

    def test_unrecognized_card_is_recorded_but_not_executed(self):
        dump, _metadata = _command_dump()
        writes: list[dict[str, object]] = []

        execution = execute_command_card_payload(
            {"rfid": "ABCD1234", "dump": dump},
            reader_id="reader-1",
            result_writer=lambda **kwargs: writes.append(kwargs) or {},
        )

        assert execution is not None
        assert execution.status == RFIDCommandExecution.Status.BLOCKED
        assert execution.status_detail == "unrecognized command card"
        assert writes == []

    def test_malformed_long_command_name_is_truncated_for_audit(self):
        long_command = "X" * 80
        dump, _metadata = _command_dump(command=long_command, params={})

        execution = execute_command_card_payload(
            {"rfid": "ABCD1234", "dump": dump},
            reader_id="reader-1",
            result_writer=lambda **kwargs: pytest.fail("handler should stay blocked"),
        )

        assert execution is not None
        assert execution.status == RFIDCommandExecution.Status.BLOCKED
        assert execution.status_detail == "unrecognized command card"
        assert execution.command_name == "X" * 64

    def test_payload_digest_mismatch_blocks_execution_without_writing(self):
        dump, metadata = _command_dump(command="NOOP", params={"channel": "stable"})
        tag = RFID.objects.create(
            rfid="ABCD1234",
            command_card_name="Suite Upgrade",
            command_card_metadata=metadata.as_dict(),
            command_payload_digest=_command_payload_digest(command="LOG"),
            command_provenance_key=metadata.provenance_key,
        )
        writes: list[dict[str, object]] = []

        execution = execute_command_card_payload(
            {"rfid": "ABCD1234", "label_id": tag.pk, "dump": dump},
            reader_id="reader-1",
            result_writer=lambda **kwargs: writes.append(kwargs) or {},
        )

        assert execution is not None
        assert execution.status == RFIDCommandExecution.Status.BLOCKED
        assert execution.status_detail == "card command payload does not match database"
        assert writes == []

    def test_lifecycle_metadata_mismatch_blocks_execution_without_writing(self):
        dump, metadata = _command_dump()
        for entry in dump:
            if entry["block"] == command_layout.COMMAND_METADATA_BLOCK:
                entry["data"][7] |= command_layout.COMMAND_METADATA_READER_HELD_FLAG
                break
        tag = RFID.objects.create(
            rfid="ABCD1234",
            command_card_name="Suite Upgrade",
            command_card_metadata=metadata.as_dict(),
            command_payload_digest=_command_payload_digest(),
            command_provenance_key=metadata.provenance_key,
        )
        writes: list[dict[str, object]] = []

        execution = execute_command_card_payload(
            {"rfid": "ABCD1234", "label_id": tag.pk, "dump": dump},
            reader_id="reader-1",
            result_writer=lambda **kwargs: writes.append(kwargs) or {},
        )

        assert execution is not None
        assert execution.status == RFIDCommandExecution.Status.BLOCKED
        assert (
            execution.status_detail == "card command lifecycle does not match database"
        )
        assert writes == []

    def test_provenance_mismatch_blocks_execution(self):
        dump, metadata = _command_dump()
        tag = RFID.objects.create(
            rfid="ABCD1234",
            command_card_name="Suite Upgrade",
            command_card_metadata=metadata.as_dict(),
            command_provenance_key="0011223344556677",
        )
        writes: list[dict[str, object]] = []

        execution = execute_command_card_payload(
            {"rfid": "ABCD1234", "label_id": tag.pk, "dump": dump},
            reader_id="reader-1",
            result_writer=lambda **kwargs: writes.append(kwargs) or {},
        )

        assert execution is not None
        assert execution.status == RFIDCommandExecution.Status.BLOCKED
        assert execution.status_detail == "unrecognized command card"
        assert writes == []
