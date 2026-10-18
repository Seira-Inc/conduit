import json

from mcp.shared.memory import create_connected_server_and_client_session

from conduit.browser import browser_tools
from conduit.mcp_server import build_server
from conduit.models import Manifest
from conduit.programs import ProgramError
from conduit.runtime import Runtime
from conduit.session_lock import SessionBusy
from conduit.tool_results import error_payload, error_result, success_result


def test_program_error_payload_keeps_the_fields_verify_export_reads():
    exc = ProgramError("postcondition_failed", "The record did not settle", uncertain=True)
    exc.object_id = "rec_9"
    exc.postcondition = {"field": "name"}
    exc.observations = ["one"]
    payload = error_payload(exc)
    assert payload == {
        "status": "error", "code": "postcondition_failed", "message": "The record did not settle",
        "outcome_unknown": True, "mutation_retried": False, "object_id": "rec_9",
        "postcondition": {"field": "name"}, "execution_observations": ["one"],
    }
    result = error_result(exc)
    assert result.isError and json.loads(result.content[0].text) == payload


def test_session_busy_is_a_coded_error():
    payload = error_payload(SessionBusy("This connector is open in another client."))
    assert payload["code"] == "session_in_use" and payload["outcome_unknown"] is False
    assert json.loads(error_result(SessionBusy("busy")).content[0].text)["code"] == "session_in_use"


def test_plain_exceptions_stay_text_and_hide_transport_detail():
    assert error_result(ValueError("bad input")).content[0].text == "bad input"
    text = error_result(ConnectionError("https://user:secret@host")).content[0].text
    assert "secret" not in text and text.startswith("ConnectionError")


def test_success_result_structures_only_with_a_schema():
    plain = success_result({"id": 1})
    assert plain.structuredContent is None and json.loads(plain.content[0].text) == {"id": 1}
    assert success_result({"id": 1}, {"type": "object"}).structuredContent == {"id": 1}
    image = success_result({"image_base64": "AAAA", "mime_type": "image/jpeg", "ok": True})
    assert image.content[0].type == "image" and image.content[0].mimeType == "image/jpeg"
    assert json.loads(image.content[1].text) == {"ok": True}


def test_a_receipt_that_breaks_its_schema_is_an_error_that_keeps_the_receipt():
    schema = {"type": "object", "properties": {"email": {"type": "string"}}, "required": ["email"]}
    result = success_result({"email": None}, schema)
    assert result.isError and result.structuredContent is None
    payload = json.loads(result.content[0].text)
    assert payload["code"] == "receipt_schema_mismatch" and payload["receipt"] == {"email": None}
    assert payload["message"].startswith("Receipt did not match the tool's declared output schema")


async def test_connector_server_never_serves_notes(tmp_path):
    manifest = Manifest(
        id="demo", name="Demo", kind="website", target="https://example.com",
        allowed_origins=["https://example.com"], tools=browser_tools(),
        notes="SECRET-NOTE: create the contact John Doe",
    )
    server = build_server(manifest, Runtime(manifest, tmp_path))
    assert "SECRET-NOTE" not in server.instructions
    async with create_connected_server_and_client_session(server) as session:
        report = await session.read_resource("conduit://discovery")
        assert "SECRET-NOTE" not in report.contents[0].text
        assert json.loads(report.contents[0].text)["name"] == "Demo"
