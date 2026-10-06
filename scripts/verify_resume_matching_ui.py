"""Synthetic UI checks; never calls live hiring operations."""
import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

base = os.getenv("TALENTPILOT_URL", "http://127.0.0.1:8769")
output = Path(__file__).resolve().parents[1] / "data" / "verification-resume-matching"
output.mkdir(parents=True, exist_ok=True)
errors, results = [], []
with sync_playwright() as p:
    browser = p.chromium.launch(
        executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", headless=True)
    page = browser.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/api/platform/resume-match/overview", lambda route: route.fulfill(json={
        "folders": [{"path": "实习生/测试岗位", "files": 2}], "profiles": [], "latest": None,
        "jev": {"configured": True, "model": "jev-1.13.0"}}))
    calls = []
    def preview(route):
        calls.append(route.request.post_data_json)
        route.fulfill(json={"batch_id": "b" * 32, "status": "completed",
                            "position_count": 1, "matched_jobs": 1, "matched_folders": 1,
                            "resume_count": 2, "high_count": 1, "parse_failed": 0,
                            "results": [{"run_id": "a" * 32, "job_id": "J123",
                                         "job_title": "测试岗位"}], "errors": []})
    page.route("**/api/platform/resume-match/all", preview)
    page.goto(base + "/console")
    page.wait_for_function("document.querySelector('#organizer-state').textContent.includes('自动更新')")
    assert page.locator("#organizer-report").count() == 0
    page.locator("#resume-match").click()
    page.wait_for_function("document.querySelector('#organizer-match-result').textContent.includes('自动匹配 1/1')")
    assert calls == [{}]
    for width in (1440, 390, 320):
        page.set_viewport_size({"width": width, "height": 900})
        section = page.locator("#resume-match").locator("xpath=ancestor::section")
        section.scroll_into_view_if_needed()
        page.screenshot(path=str(output / f"console-{width}.png"))
        overflow = page.evaluate("document.documentElement.scrollWidth > innerWidth")
        results.append({"width": width, "overflow": overflow})
        assert not overflow
    browser.close()
assert not errors, errors
print(json.dumps({"viewports": results, "errors": errors, "preview_clicks": len(calls),
                  "live_external_actions": 0}))
