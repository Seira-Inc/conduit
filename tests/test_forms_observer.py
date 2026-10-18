import pytest

from scripts.verify_forms_export import FormsComparisonError, FormsObserver, FormsReadError, check_record


def test_native_observer_rejects_fabricated_title_and_unrelated_changes():
    original = {"id": "f1", "title": "Conduit before", "description": "Keep", "modifiedDate": "old"}
    expected = {**original, "title": "Conduit after", "modifiedDate": "new"}
    result = {"id": "f1", "data": expected}
    with pytest.raises(AssertionError, match="independently read"):
        check_record("forms", "update", {"title": "Conduit after"}, result, original, original)
    changed = {**expected, "description": "Lost"}
    with pytest.raises(AssertionError, match="unrelated"):
        check_record("forms", "update", {"title": "Conduit after"}, {"id": "f1", "data": changed}, changed, original)
    actual, check = check_record("forms", "update", {"title": "Conduit after"}, result, expected, original)
    assert actual == expected and check["unrelated_properties_preserved"]


def test_native_question_identity_and_absence_are_checked():
    native = {"id": "q1", "title": "Conduit question", "type": "Question.TextField"}
    result = {"id": "f1::q1", "data": {**native, "id": "f1::q1", "form_id": "f1", "question_id": "q1"}}
    actual, check = check_record("form_questions", "create", {"title": native["title"]}, result, native)
    assert actual == result["data"] and check["native_record_matches"]
    with pytest.raises(AssertionError, match="different question"):
        check_record("form_questions", "read", {}, result, {**native, "id": "q2"})
    with pytest.raises(AssertionError, match="independently read"):
        check_record("form_questions", "delete", {}, {"id": "f1::q1", "data": None}, native)
    assert check_record("form_questions", "delete", {}, {"id": "f1::q1", "data": None}, None)[1]["absent"]


async def test_question_observer_uses_collection_even_if_single_record_route_is_missing():
    observer = FormsObserver(None)
    observer.base = "https://forms.example/forms"
    native = {"id": "q1", "title": "Conduit", "status": "Active"}

    async def get(url):
        return {"value": [native]} if url.endswith("/questions") else None

    observer.get = get
    assert await observer.record("form_questions", "f1::q1") == native
    assert await observer.record("form_questions", "f1::missing") is None


async def test_missing_collection_is_not_proof_of_deletion_when_parent_exists():
    observer = FormsObserver(None)
    observer.base = "https://forms.example/forms"

    async def get(url):
        return None if url.endswith("/questions") else {"id": "f1"}

    observer.get = get
    with pytest.raises(AssertionError, match="parent still exists"):
        await observer.record("form_questions", "f1::q1")


@pytest.mark.parametrize("statuses,passes", [([401, 401, 200], True), ([500], False)])
async def test_startup_waits_for_auth_read_but_does_not_retry_server_failures(monkeypatch, statuses, passes):
    from types import SimpleNamespace

    async def noop(*args, **kwargs):
        pass

    async def evaluate(*args):
        return {"tenant": "tenant", "user": "user"}

    browser = SimpleNamespace(start=noop, page=SimpleNamespace(wait_for_function=noop, evaluate=evaluate))
    observer = FormsObserver(browser)
    replies = iter(statuses)
    calls = []

    async def get(url):
        calls.append(url)
        status = next(replies)
        if status != 200:
            raise FormsReadError(status)
        return {"value": []}

    observer.get = get
    monkeypatch.setattr("scripts.verify_forms_export.asyncio.sleep", noop)
    if passes:
        await observer.start()
    else:
        with pytest.raises(FormsReadError):
            await observer.start()
    assert len(calls) == len(statuses)


def test_child_edits_preserve_parent_content_except_its_revision():
    from scripts.verify_forms_workflow import check_parent

    original = {"id": "parent", "title": "Keep", "modifiedDate": "old"}
    check_parent(original, {**original, "modifiedDate": "new"})
    with pytest.raises(AssertionError, match="unrelated parent"):
        check_parent(original, {**original, "title": "Lost"})


def test_optional_row_count_type_annotation_does_not_hide_changed_content():
    from scripts.verify_forms_workflow import check_parent

    annotated = {"id": "parent", "rowCount": 0, "rowCount@odata.type": "#Int64"}
    plain = {"id": "parent", "rowCount": 0}
    assert check_parent(annotated, plain) == {"rowCount@odata.type": {"before": "#Int64", "after": None}}
    assert check_parent(plain, annotated)
    for changed in ({**plain, "rowCount": 1}, {**plain, "id": "other"},
                    {**annotated, "rowCount@odata.type": "#String"}):
        with pytest.raises(AssertionError, match="unrelated parent"):
            check_parent(annotated, changed)


def test_comparison_failure_keeps_field_evidence_without_copying_content():
    from conduit.mcp_check import failure_detail

    original = {"id": "f1", "title": "Private title", "rowCount": 0}
    changed = {"id": "f1", "title": "Different private title", "rowCount": 1}
    with pytest.raises(FormsComparisonError) as error:
        check_record("forms", "read", {}, {"id": "f1", "data": original}, changed)
    report = failure_detail(error.value)
    assert report["error_code"] == "independent_record_mismatch"
    fields = report["postcondition"]["differences"]
    assert fields["rowCount"]["before"] == 0
    assert fields["rowCount"]["after"] == 1
    assert "title" in fields and "before" not in fields["title"]


def test_record_comparison_allows_only_the_observed_type_annotation_omission():
    original = {"id": "f1", "title": "Keep", "rowCount@odata.type": "#Int64", "rowCount": 0}
    plain = {"id": "f1", "title": "Keep", "rowCount": 0}
    actual, check = check_record("forms", "read", {}, {"id": "f1", "data": original}, plain, original)
    assert actual == plain and check["optional_type_annotations"] == ["rowCount@odata.type"]
    assert check_record("forms", "read", {}, {"id": "f1", "data": plain}, original, plain)[0] == original
    for wrong in ({**plain, "rowCount": 1}, {**plain, "title": "Lost"},
                  {**original, "rowCount@odata.type": "#String"}, {**original, "rowCount": "0"}):
        with pytest.raises(FormsComparisonError):
            check_record("forms", "read", {}, {"id": "f1", "data": original}, wrong, original)
