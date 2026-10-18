from conduit.compiler_model import parse_payload


def test_missing_step_closer_can_be_completed_without_changing_data():
    value = parse_payload('{"action":"explore","steps":[{"kind":"click",'
                          '"locator":{"role":"menuitem","name":"Open"}]}')
    assert value["steps"] == [{"kind": "click", "locator": {"role": "menuitem", "name": "Open"}}]
    assert value["syntax_recovery"] == {"inserted_closers": 1}


def test_json_completion_does_not_invent_values_or_complete_truncated_programs():
    for payload in ('{"action":"propose","family":', '{"action":"explore","steps":[',
                    '{"action":"explore","reason":"unterminated', '{"value":}',
                    '{"a":1 "b":2}', '{"action":"explore"}}'):
        assert parse_payload(payload)["action"] == "invalid"
    assert parse_payload('{"action":"done","reason":"braces [ } ] and \\" quote"}') == {
        "action": "done", "reason": 'braces [ } ] and " quote'}
