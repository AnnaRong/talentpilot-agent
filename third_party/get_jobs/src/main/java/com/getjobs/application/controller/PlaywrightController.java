package com.getjobs.application.controller;

import com.getjobs.worker.manager.PlaywrightManager;
import com.microsoft.playwright.Page;
import com.microsoft.playwright.options.WaitUntilState;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Playwright管理控制器
 * 用于测试和管理Playwright实例
 */
@RestController
@RequestMapping("/api/playwright")
public class PlaywrightController {

    private final PlaywrightManager playwrightManager;

    public PlaywrightController(PlaywrightManager playwrightManager) {
        this.playwrightManager = playwrightManager;
    }

    /**
     * 获取Playwright状态信息
     */
    @GetMapping("/status")
    public ResponseEntity<Map<String, Object>> getStatus() {
        Map<String, Object> status = new HashMap<>();
        status.put("initialized", playwrightManager.isInitialized());
        status.put("cdpPort", playwrightManager.getCdpPort());
        status.put("hasBossPage", playwrightManager.getBossPage() != null);
        // 改用持久化上下文后没有独立的 Browser 对象，context 即代表浏览器本身
        status.put("hasBrowser", playwrightManager.getContext() != null);
        status.put("bossLoggedIn", playwrightManager.isLoggedIn("boss"));

        return ResponseEntity.ok(status);
    }

    /**
     * 指纹自检：确认当前浏览器有没有暴露自动化特征。
     * <p>
     * 期望值：
     * navigator.webdriver = false（真实 Chrome 就是 false，undefined 反而是老式 stealth 脚本的痕迹）；
     * userAgentData 必须存在且 platform/brands 与 UA 自洽；cdc_ 残留为空。
     */
    @GetMapping("/fingerprint")
    public ResponseEntity<Map<String, Object>> fingerprint() {
        try {
            // Playwright 调用必须走专用线程，不能直接在 http-nio 线程上调
            Object raw = playwrightManager.callOnPlaywright(() -> playwrightManager.getBossPage().evaluate("""
                    () => {
                        const uaData = navigator.userAgentData;
                        return {
                            'navigator.webdriver': String(navigator.webdriver),
                            'userAgent': navigator.userAgent,
                            'navigator.platform': navigator.platform,
                            'uaData.platform': uaData ? uaData.platform : '(缺失-异常)',
                            'uaData.brands': uaData && uaData.brands
                                ? uaData.brands.map(b => b.brand + ' ' + b.version).join(', ')
                                : '(缺失-异常)',
                            'cdc_残留': Object.keys(window).filter(k => k.startsWith('cdc_')).join(',') || '(无)',
                            'url': location.href
                        };
                    }"""));
            Map<String, Object> result = new HashMap<>();
            result.put("success", true);
            result.put("fingerprint", raw);
            return ResponseEntity.ok(result);
        } catch (Exception e) {
            Map<String, Object> error = new HashMap<>();
            error.put("success", false);
            error.put("error", e.getMessage());
            return ResponseEntity.internalServerError().body(error);
        }
    }

    /**
     * 诊断：对比"常驻的 bossPage 标签页"和"新开的受控标签页"加载同一地址的表现。
     * <p>
     * 现象是 bossPage 一直转圈加载不完，但在同一个浏览器里手动新开标签页打开同样的地址却正常。
     * 同浏览器同 profile 同指纹，差别只可能在标签页本身，这里把这个变量隔离出来。
     */
    @GetMapping("/diagnose")
    public ResponseEntity<Map<String, Object>> diagnose(
            @RequestParam(defaultValue = "https://www.zhipin.com/web/geek/jobs?query=Java&city=101020100") String url) {
        Map<String, Object> out = new LinkedHashMap<>();
        String probeJs = """
                () => ({
                  readyState: document.readyState,
                  title: document.title,
                  url: location.href,
                  bodyLength: document.body ? document.body.innerHTML.length : 0,
                  jobCards: document.querySelectorAll('li.job-card-box, li.job-card-wrapper').length,
                  listContainers: document.querySelectorAll('ul.rec-job-list, ul.job-list-box, .job-list-box').length,
                  pending: performance.getEntriesByType('resource')
                             .filter(r => r.responseEnd === 0).map(r => r.name).slice(0, 8)
                })""";
        // 刻意不碰常驻的 bossPage：它一旦卡在"永久加载"，evaluate 会无限期阻塞（没有默认超时），
        // 整个诊断请求也会跟着挂死。这里只用全新的受控标签页做对照。
        String[] targets = {"https://www.zhipin.com", url};
        try {
            playwrightManager.callOnPlaywright(() -> {
                for (String target : targets) {
                    Map<String, Object> result = new LinkedHashMap<>();
                    Page probe = null;
                    long start = System.currentTimeMillis();
                    try {
                        probe = playwrightManager.getContext().newPage();
                        probe.setDefaultTimeout(20_000);
                        probe.navigate(target, new Page.NavigateOptions()
                                .setTimeout(20_000)
                                .setWaitUntil(WaitUntilState.DOMCONTENTLOADED));
                        result.put("navigateMs", System.currentTimeMillis() - start);
                        probe.waitForTimeout(5_000);
                        result.put("currentUrl", probe.url());
                        result.put("probe", probe.evaluate(probeJs));
                    } catch (Exception e) {
                        result.put("elapsedMs", System.currentTimeMillis() - start);
                        result.put("error", e.getMessage() == null ? e.toString()
                                : e.getMessage().split("\n")[0]);
                    } finally {
                        if (probe != null) {
                            try {
                                probe.close();
                            } catch (Exception ignored) {
                            }
                        }
                    }
                    out.put(target, result);
                }
                return null;
            });
            out.put("success", true);
        } catch (Exception e) {
            out.put("success", false);
            out.put("error", e.getMessage());
        }
        return ResponseEntity.ok(out);
    }

    /**
     * 测试Boss导航功能
     */
    @GetMapping("/test-navigate")
    public ResponseEntity<Map<String, String>> testNavigate() {
        try {
            playwrightManager.getBossPage().navigate("https://www.zhipin.com");
            String title = playwrightManager.getBossPage().title();

            Map<String, String> result = new HashMap<>();
            result.put("success", "true");
            result.put("title", title);
            result.put("url", playwrightManager.getBossPage().url());

            return ResponseEntity.ok(result);
        } catch (Exception e) {
            Map<String, String> error = new HashMap<>();
            error.put("success", "false");
            error.put("error", e.getMessage());
            return ResponseEntity.internalServerError().body(error);
        }
    }
}
