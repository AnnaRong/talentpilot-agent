"""Local UI regression checks; paper-radar requests use synthetic responses."""
import os
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    output = Path(__file__).resolve().parents[1] / "data/verification-paper-radar"
    output.mkdir(parents=True, exist_ok=True)
    errors, requests = [], []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            headless=True,
        )
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))

        def radar_response(route):
            requests.append(route.request.post_data_json)
            route.fulfill(json={
                "run_id": "synthetic-ui-only", "status": "completed",
                "profile": {}, "sources": [], "papers": [],
                "candidates": [], "candidate_count": 0,
            })

        page.route("**/api/platform/connectors/paper-radar/run", radar_response)
        page.goto(os.getenv("TALENTPILOT_URL", "http://127.0.0.1:8768") + "/console")
        trigger = page.locator("#paper-radar-trigger")
        workspace = page.locator("#paper-radar-workspace")
        expect(trigger).to_be_enabled()
        expect(workspace).to_be_hidden()
        expect(trigger).to_have_attribute("aria-expanded", "false")
        # Job-dependent sources must not disable the independent radar entry.
        page.locator("#target-job").dispatch_event("change")
        expect(trigger).to_be_enabled()
        trigger.click()
        expect(page.locator("#radar-jd")).to_be_focused()
        for width in (1440, 390, 320):
            page.set_viewport_size({"width": width, "height": 720})
            trigger.evaluate("(el) => el.scrollIntoView({block:'start'})")
            expect(page.locator("#notice")).not_to_be_in_viewport()
            for value in ("", "   ", "123456789"):
                page.locator("#radar-jd").fill(value)
                page.locator("#radar-jd-confirm").click()
                expect(page.locator("#radar-jd-error")).to_contain_text("至少 10 个字符")
                expect(page.locator("#radar-jd-error")).to_be_in_viewport()
                expect(page.locator("#radar-jd")).to_have_attribute("aria-invalid", "true")
                expect(page.locator("#radar-jd")).to_be_focused()
                expect(page.locator("#paper-radar-page-1")).to_be_visible()
            assert not requests
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            trigger.locator("xpath=ancestor::section").screenshot(
                path=str(output / f"validation-{width}.png"))
        page.set_viewport_size({"width": 1440, "height": 1000})
        jd = "SYNTHETIC TEST ONLY: AI researcher, machine learning, Beijing"
        page.locator("#radar-jd").fill(jd)
        trigger.click()
        expect(workspace).to_be_hidden()
        trigger.click()
        expect(page.locator("#radar-jd")).to_have_value(jd)
        page.locator("#radar-jd-confirm").click()
        expect(page.locator("#radar-jd-error")).to_be_hidden()
        expect(page.locator("#radar-jd")).not_to_have_attribute("aria-invalid", "true")
        expect(page.locator("#paper-radar-page-2")).to_be_visible()
        trigger.click()
        trigger.click()
        expect(page.locator("#radar-year-from")).to_be_focused()
        page.locator("#radar-jd-back").click()
        expect(page.locator("#radar-jd")).to_have_value(jd)
        page.locator("#radar-jd-confirm").click()
        page.locator("#radar-year-from").fill("2020")
        page.locator("#radar-year-to").fill("2026")
        page.locator("#paper-radar-run").click()
        expect(page.locator("#paper-radar-state")).to_contain_text("已完成：0 篇论文")
        assert requests == [{
            "jd": jd, "sources": ["arXiv", "OpenAlex", "Semantic Scholar",
                                  "DBLP", "Crossref", "会议官网"],
            "limit": 8, "year_from": 2020, "year_to": 2026,
        }]
        page.locator("#radar-jd-back").click()
        for width in (1440, 390, 320):
            page.set_viewport_size({"width": width, "height": 900})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            section = trigger.locator("xpath=ancestor::section")
            section.screenshot(path=str(output / f"expanded-{width}.png"))
            trigger.click()
            section.screenshot(path=str(output / f"collapsed-{width}.png"))
            trigger.click()
        assert not errors, errors
        browser.close()
    print("PASS: entry, focus, validation, retained state, unchanged request, "
          "desktop/mobile layout; synthetic radar requests only.")


if __name__ == "__main__":
    main()
