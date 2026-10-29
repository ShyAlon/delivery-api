"""Local-only mocked question-quality editor contract, not cloud configuration.

Owner Q5 (2026-10-28) retains this test only in local mode. Cloud settings are
service-managed. Shared isolated legacy fixtures remain retirement debt.
"""
import json
from test_local_rerun_browser import ui, page  # noqa: F401
from playwright.sync_api import expect

QQ_DEFAULTS={"retrieval":{"max_words":90,"max_chars":600,"top_k":4},"verification":{"retry_enabled":False,"grounding_overlap_min":1},
             "integrations":{"none_stated_enabled":True,"external_system_verbs":["connect","sync"]},"area_cues":{"data":["from","own"]}}


def test_debug_dialog_edits_question_quality_settings(page, ui):
    state={"config":json.loads(json.dumps(QQ_DEFAULTS)),"puts":[]}
    def handler(route):
        req=route.request
        if req.method=="GET":return route.fulfill(json={"config_version":1,"config":state["config"],"defaults":QQ_DEFAULTS})
        body=json.loads(req.post_data)["config"];state["puts"].append(body)
        if body["retrieval"]["max_words"]>300:return route.fulfill(status=422,json={"detail":{"error":"retrieval.max_words must be an integer from 20 to 300","cause":"The engine validates every setting before saving.","next":"Fix that setting and save again. Nothing was changed."}})
        state["config"]=body;route.fulfill(json={"config_version":1,"config":body})
    page.route("**/api/question-quality-config",handler)
    page.goto(ui+"/");page.locator("#accountbutton").click();page.locator("#opendebug").click();page.locator(".debug-dialog").wait_for()
    page.get_by_text("Question quality settings",exact=True).click()
    words=page.locator("#qq-retrieval-max_words");words.wait_for();assert words.input_value()=="90"
    assert page.locator("#qq-verification-retry_enabled").is_checked() is False
    assert page.locator("#qq-integrations-external_system_verbs").input_value()=="connect, sync"
    page.screenshot(path="/tmp/d1-debug-qq.png");page.locator("#qqsave").scroll_into_view_if_needed();page.screenshot(path="/tmp/d1-debug-qq2.png")
    page.locator("#qq-verification-retry_enabled").check();words.fill("60");page.locator("#qq-area_cues-data").fill("from, own, bundled")
    page.locator("#qqsave").click();expect(page.locator("#qqstatus")).to_contain_text("Saved")
    assert state["puts"][-1]["retrieval"]["max_words"]==60 and state["puts"][-1]["verification"]["retry_enabled"] is True
    assert state["puts"][-1]["area_cues"]["data"]==["from","own","bundled"]
    words.fill("999");page.locator("#qqsave").click();expect(page.locator("#qqstatus")).to_contain_text("retrieval.max_words")
    assert state["config"]["retrieval"]["max_words"]==60
    page.locator("#qqreset").click();assert page.locator("#qq-retrieval-max_words").input_value()=="90"
    assert state["config"]["retrieval"]["max_words"]==60  # reset only fills the form; nothing is saved until Save
    page.get_by_role("button",name="Close debug").click()

