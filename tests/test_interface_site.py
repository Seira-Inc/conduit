import pytest
from interface_site import website

from conduit.models import Manifest
from conduit.runtime import Runtime


@pytest.mark.integration
@pytest.mark.parametrize("variant", [False, True])
async def test_html_fixture_has_real_form_crud_without_application_javascript_or_json(tmp_path, variant):
    with website(variant) as (url, state):
        manifest = Manifest(id="html-fixture", name="HTML fixture", target=url, kind="website",
                            allowed_origins=[url], allow_private=True)
        runtime = Runtime(manifest, tmp_path)
        try:
            await runtime.browser.start()
            page = runtime.browser.page
            await page.get_by_role("button", name="Sign in").click()
            await page.get_by_role("heading", name="My records").wait_for()
            await page.get_by_role("link", name="New record").click()
            await page.get_by_label("Caption" if variant else "Title", exact=True).fill('Conduit "HTML" café')
            await page.get_by_label("Note", exact=True).fill("Keep this note")
            await page.get_by_role("button", name="Create record").click()
            await page.get_by_role("heading", name="Record", exact=True).wait_for()
            key = page.url.rsplit("/", 1)[1]
            assert state["items"]["tester"][key] == {"id": key, state["field"]: 'Conduit "HTML" café',
                                                      "note": "Keep this note", "version": 1}
            await page.get_by_role("link", name="Edit record").click()
            old_token = await page.locator('meta[name="csrf-token"]').get_attribute("content")
            await page.get_by_label("Caption" if variant else "Title", exact=True).fill("Conduit revised")
            await page.get_by_role("button", name="Save changes").click()
            await page.get_by_role("heading", name="Record", exact=True).wait_for()
            assert state["items"]["tester"][key]["note"] == "Keep this note"
            assert state["items"]["tester"][key]["version"] == 2
            denied = await runtime.browser.context.request.post(url + state["base"] + "/" + key + "/remove",
                                                               form={"csrf": old_token})
            assert denied.status == 403 and key in state["items"]["tester"]
            assert await page.locator("script").count() == 0
            await page.get_by_role("button", name="Delete record").click()
            await page.get_by_role("heading", name="My records").wait_for()
            assert not state["items"]["tester"] and state["writes"] == 3
            assert state["content_types"] == {"text/html"}
        finally:
            await runtime.close()
