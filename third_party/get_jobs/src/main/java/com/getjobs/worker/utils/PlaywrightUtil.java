package com.getjobs.worker.utils;

import lombok.extern.slf4j.Slf4j;

import java.util.concurrent.TimeUnit;

/**
 * 线程休眠工具。
 * <p>
 * 这里原来还有一整套独立的浏览器初始化（launch + newContext + 伪造 UA + addInitScript 注入 stealth 脚本），
 * 那是迁到 patchright 之前的旧方案，全部是反检测的泄漏点，已删除。
 * 浏览器统一由 {@link com.getjobs.worker.manager.PlaywrightManager} 管理，不要在这里再起一个。
 */
@Slf4j
public class PlaywrightUtil {

    /**
     * 等待指定时间（秒）
     *
     * @param seconds 等待的秒数
     */
    public static void sleep(int seconds) {
        try {
            TimeUnit.SECONDS.sleep(seconds);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            log.error("Sleep被中断", e);
        }
    }
}
