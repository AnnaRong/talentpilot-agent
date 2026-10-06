/**
 * 前端应用配置文件
 * 用于配置应用的运行参数，替代 .env 文件
 */
module.exports = {
  // 服务器端口
  port: 6866,

  // 开发环境配置
  development: {
    // 是否开启 Turbopack
    turbo: true,
    // 是否自动打开浏览器
    open: true,
    // 绑定到 IPv4 避免在 Windows 上 ::1 权限问题
    hostname: '127.0.0.1',
  },

  // 生产环境配置
  production: {
    // 生产环境端口
    port: 6866,
    // 主机名
    hostname: '0.0.0.0',
  },

  // API 配置（如果需要在构建时使用）
  api: {
    // 后端 API 地址，需与 application.yaml 的 server.port 一致
    baseUrl: 'http://localhost:9527',
  },

  // 其他自定义配置
  app: {
    name: 'Get Jobs',
    version: '1.0.0',
  }
}
