"""Local UI verification only. Never automates external hiring writes."""
from __future__ import annotations

import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    output = ROOT / "data" / "verification-overseas"
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    base_url = os.getenv("TALENTPILOT_URL", "http://127.0.0.1:8765")
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base_url + "/console")
        page.locator("#workspace").wait_for(state="visible")
        page.wait_for_function("document.querySelector('#report-source').textContent !== '正在读取报表'")
        page.evaluate("""
          state.positions=[{job_id:'J_SYNTHETIC',title:'海外业务经理',department:'TEST',first_interview_count:0,second_interview_count:0,third_interview_count:0}];
          const select=document.querySelector('#target-job');
          select.innerHTML='<option value="J_SYNTHETIC">海外业务经理（J_SYNTHETIC）</option>';
          select.dispatchEvent(new Event('change'));
        """)
        page.route("**/api/platform/connectors/platform-advisor/interview", lambda route: route.fulfill(json={
            "status": "ready", "complete": True, "summary": ["SYNTHETIC UI ONLY"],
            "adjustments": [], "model": "synthetic-llm", "llm_calls": 1,
            "external_hiring_actions": 0,
        }))
        page.evaluate("state.advisorStatus={configured:true,missing:[]}")
        page.locator('[data-source="邮箱"]').click()
        page.locator("#email-dialog").wait_for(state="visible")
        email_visible = page.locator("#email-dialog-title").inner_text() == "邮箱 Agent 收集简历"
        email_login_visible = page.locator("#email-login").is_visible()
        email_login_check_visible = page.locator("#email-check-login").is_visible()
        email_agent_resume_visible = page.locator("#email-resume").is_visible()
        page.locator("#email-cancel").click()
        page.evaluate("state.emailRun=null")
        page.locator('[data-source="海外"]').click()
        page.locator("#overseas-dialog").wait_for(state="visible")
        page.wait_for_timeout(300)
        overseas_visible = page.locator("#overseas-dialog-title").inner_text() == "海外人才采集与简历下载"
        extension_action_visible = page.locator("#overseas-extension").is_visible()
        page.screenshot(path=str(output / "console-desktop.png"), full_page=True)
        desktop_overflow = page.evaluate("document.documentElement.scrollWidth > innerWidth")
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(150)
        page.screenshot(path=str(output / "console-mobile.png"), full_page=True)
        mobile_overflow = page.evaluate("document.documentElement.scrollWidth > innerWidth")
        sections = page.locator("#workspace h2").all_text_contents()
        browser.close()
    result = {"desktop_overflow": desktop_overflow,
              "mobile_overflow": mobile_overflow, "page_errors": errors, "sections": sections,
              "overseas_visible": overseas_visible,
              "extension_action_visible": extension_action_visible,
              "email_visible": email_visible,
              "email_login_visible": email_login_visible,
              "email_login_check_visible": email_login_check_visible,
              "email_agent_resume_visible": email_agent_resume_visible,
              "real_external_write": False, "recording_user_selection_verified": False}
    (output / "ui-verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))
    assert (overseas_visible and extension_action_visible and email_visible and email_login_visible
            and email_login_check_visible
            and email_agent_resume_visible and not errors and not desktop_overflow and not mobile_overflow)


if __name__ == "__main__":
    main()
