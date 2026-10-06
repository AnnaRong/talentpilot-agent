"""Desktop/mobile browser checks using synthetic rows, never external writes."""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    root = Path(__file__).resolve().parents[1]
    output = root / "data" / "verification-lock-monitor"
    output.mkdir(parents=True, exist_ok=True)
    base = os.getenv("TALENTPILOT_URL", "http://127.0.0.1:8767")
    now = datetime.now(timezone.utc)
    rows = []
    for source, name, state, hours in [
        ("upload", "测试候选人甲", "expiring", 4), ("upload", "测试候选人乙", "locked", 48),
        ("recruiting", "测试候选人丙", "verify_due", -1), ("followed", "测试候选人丁", "unlocked", None),
        ("recruiting", "测试候选人戊", "unknown", None), ("recruiting", "测试候选人己", "stale", None)]:
        rows.append({"id": str(len(rows)+1)*64, "source": source, "name": name, "state": state,
                     "job_title": "AI产品经理实习生", "job_id": "J_SYNTHETIC", "fresh": True,
                     "locked": state in {"locked", "expiring", "verify_due", "unknown"},
                     "observed_at": now.isoformat(), "provenance": "upload_blocked" if source == "upload" else None,
                     "unlock_at": (now + timedelta(hours=hours)).isoformat() if hours is not None else None,
                     "scope": "SYNTHETIC / 测试范围", "lock_label": "测试锁状态"})
    rows[-1]["fresh"] = False
    fixture = {"settings": {"enabled": False, "interval_minutes": 30, "warning_hours": 24},
               "items": rows, "notifications": [], "counts": {"locked": 1, "expiring": 1, "verify_due": 1,
               "unlocked": 1, "unknown": 1, "stale": 1}, "server_now": now.isoformat(),
               "running": False, "last_success": now.isoformat(), "last_error": None,
               "next_due": None, "skill_installed": True, "recall_write_enabled": False}
    errors, requests, results = [], [], {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1050})
        page.on("pageerror", lambda error: errors.append(str(error)))

        def monitor(route):
            requests.append(route.request.url)
            if route.request.url.endswith("/settings"):
                fixture["settings"] = route.request.post_data_json
                route.fulfill(json=fixture["settings"])
            elif route.request.url.endswith("/recall-preview"):
                route.fulfill(json={"job_id": "J_SYNTHETIC", "job_title": "测试岗位",
                                    "target_stage": "简历复筛/进行中", "checks": ["实时锁和权限", "招聘类型", "重复申请"]})
            else:
                route.fulfill(json=fixture)
        page.route("**/api/platform/lock-monitor**", monitor)
        page.goto(base + "/console")
        page.locator("#lock-table tbody tr").first.wait_for()
        assert page.get_by_text("只读巡检计划", exact=True).count() == 0
        assert page.locator(".lock-metric strong").all_inner_texts() == ["4", "2", "1", "1"]
        assert page.locator("#lock-table tbody tr").count() == 2
        page.locator("#lock-monitor").scroll_into_view_if_needed()
        page.locator("#lock-monitor").screenshot(path=str(output / "desktop-synthetic.png"))
        page.locator("#lock-search").fill("不存在")
        assert page.locator(".lock-empty b").inner_text() == "没有符合条件的候选人"
        page.locator("#lock-search").fill("")
        page.locator("#lock-tab-recruiting").click()
        assert page.locator("#lock-table tbody tr").count() == 3
        page.locator("#lock-filter").select_option("unknown")
        assert page.locator("#lock-table tbody tr").count() == 2
        page.locator("#lock-filter").select_option("all")
        page.locator("#lock-settings-open").click()
        page.locator("#lock-warning").fill("12")
        page.locator("#lock-settings button[type=submit]").click()
        page.wait_for_function("document.querySelector('#lock-settings').classList.contains('hidden')")
        assert fixture["settings"]["warning_hours"] == 12
        page.locator("#lock-refresh").click()
        page.wait_for_function("!document.querySelector('#lock-refresh').disabled")
        page.evaluate("""state.positions=[{job_id:'J_SYNTHETIC',title:'测试岗位'}]""")
        page.locator("#lock-tab-followed").click()
        page.locator("[data-lock-recall]").click()
        page.locator("#lock-target-job").select_option("J_SYNTHETIC")
        page.locator("#lock-target-stage").fill("简历复筛/进行中")
        page.locator("#lock-recall-form button[type=submit]").click()
        page.get_by_text("写入接口与逐人回读尚未验真，未执行回捞。", exact=True).wait_for()
        page.locator("#lock-recall-close").click()
        page.locator("#lock-tab-upload").click()
        for width in (1440, 768, 390, 320):
            page.set_viewport_size({"width": width, "height": 900})
            page.wait_for_timeout(150)
            results[str(width)] = page.evaluate("document.documentElement.scrollWidth<=innerWidth")
            assert results[str(width)], f"Horizontal overflow at {width}"
            if width == 390:
                page.locator("#lock-monitor").screenshot(path=str(output / "mobile-synthetic.png"))
        page.unroute("**/api/platform/lock-monitor**", monitor)
        page.set_viewport_size({"width": 1440, "height": 1050})
        page.reload()
        page.wait_for_function("!document.querySelector('#lock-health').textContent.includes('正在读取监测状态')")
        page.locator("#lock-tab-recruiting").click()
        page.locator("#lock-monitor").screenshot(path=str(output / "desktop-live.png"))
        results["live_state"] = page.locator("#lock-health").inner_text()
        browser.close()
    results.update(page_errors=errors, synthetic_requests=len(requests), external_writes=0)
    (output / "verification.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(json.dumps(results, ensure_ascii=False))
    assert not errors


if __name__ == "__main__":
    main()
