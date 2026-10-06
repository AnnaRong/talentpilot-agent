package com.getjobs.application.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.getjobs.application.entity.CookieEntity;
import com.getjobs.application.mapper.CookieMapper;
import com.baomidou.mybatisplus.core.conditions.update.UpdateWrapper;
import jakarta.annotation.PostConstruct;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.util.List;

/**
 * Cookie服务类
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class CookieService {

    private final CookieMapper cookieMapper;
    private final JdbcTemplate jdbcTemplate;

    /**
     * 确保 cookie 表存在。
     * <p>
     * 曾有一个版本把 Cookie 改为存放在 cookies/&lt;platform&gt;.json，迁移完成后执行了
     * DROP TABLE cookie。现已回退为数据库存储，那批用户的库里没有这张表，启动后
     * 所有 Cookie 读写都会抛 "no such table: cookie"，表现为登录态完全不保存，
     * 而日志里只有一行 WARN，很难定位。这里按原始 DDL 补建，已存在则不动。
     */
    @PostConstruct
    public void ensureCookieTable() {
        try {
            jdbcTemplate.execute("""
                    CREATE TABLE IF NOT EXISTS cookie (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        platform VARCHAR(50) NOT NULL,
                        cookie_value TEXT NOT NULL,
                        remark TEXT,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )""");
            jdbcTemplate.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_cookie_platform ON cookie(platform)");
        } catch (Exception e) {
            log.error("创建 cookie 表失败，Cookie 将无法保存: {}", e.getMessage());
        }
    }

    /**
     * 根据平台获取Cookie
     * @param platform 平台名称（boss/zhilian/job51/liepin）
     * @return Cookie实体
     */
    public CookieEntity getCookieByPlatform(String platform) {
        LambdaQueryWrapper<CookieEntity> wrapper = new LambdaQueryWrapper<>();
        wrapper.eq(CookieEntity::getPlatform, platform)
                .orderByDesc(CookieEntity::getUpdatedAt)
                .last("LIMIT 1");
        return cookieMapper.selectOne(wrapper);
    }

    /**
     * 保存或更新Cookie
     * @param platform 平台名称
     * @param cookieValue Cookie值
     * @param remark 备注
     * @return 是否成功
     */
    public boolean saveOrUpdateCookie(String platform, String cookieValue, String remark) {
        CookieEntity existingCookie = getCookieByPlatform(platform);

        if (existingCookie != null) {
            // 更新现有Cookie
            existingCookie.setCookieValue(cookieValue);
            existingCookie.setRemark(remark);
            existingCookie.setUpdatedAt(LocalDateTime.now());
            return cookieMapper.updateById(existingCookie) > 0;
        } else {
            // 新建Cookie
            CookieEntity newCookie = new CookieEntity();
            newCookie.setPlatform(platform);
            newCookie.setCookieValue(cookieValue);
            newCookie.setRemark(remark);
            newCookie.setCreatedAt(LocalDateTime.now());
            newCookie.setUpdatedAt(LocalDateTime.now());
            return cookieMapper.insert(newCookie) > 0;
        }
    }

    /**
     * 清空指定平台的所有Cookie值（处理重复记录场景）
     * @param platform 平台名称
     * @param remark 备注
     * @return 影响行数是否大于0
     */
    public boolean clearCookieByPlatform(String platform, String remark) {
        UpdateWrapper<CookieEntity> updateWrapper = new UpdateWrapper<>();
        updateWrapper.eq("platform", platform)
                .set("cookie_value", "")
                .set("remark", remark)
                .set("updated_at", LocalDateTime.now());
        return cookieMapper.update(null, updateWrapper) > 0;
    }

    /**
     * 删除指定平台的Cookie
     * @param platform 平台名称
     * @return 是否成功
     */
    public boolean deleteCookie(String platform) {
        LambdaQueryWrapper<CookieEntity> wrapper = new LambdaQueryWrapper<>();
        wrapper.eq(CookieEntity::getPlatform, platform);
        return cookieMapper.delete(wrapper) > 0;
    }

    /**
     * 获取所有Cookie
     * @return Cookie列表
     */
    public List<CookieEntity> getAllCookies() {
        return cookieMapper.selectList(null);
    }
}
