/**
 * 后端 API 根地址 —— 全站唯一读取入口。
 *
 * 之前这个地址是逐处硬编码的：39 处 http://localhost:8888 散在 11 个文件里，
 * 改一次后端端口就得全项目搜索替换，漏一处就是页面上某个按钮静默失效。
 *
 * 取值优先级：
 *   1. NEXT_PUBLIC_API_BASE —— 临时覆盖用（NEXT_PUBLIC_ 前缀是 Next.js 注入浏览器端的要求）
 *   2. API_BASE_URL         —— next.config.ts 从 front/server.config.js 的 api.baseUrl 注入
 *   3. 兜底字面量           —— 上面都没有时才用，需与后端 application.yaml 的 server.port 一致
 *
 * 也就是说：平时改端口只改 front/server.config.js 和后端 application.yaml 两处。
 */
export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ||
  process.env.API_BASE_URL ||
  'http://localhost:9527'

/** 拼接后端接口地址，path 以 / 开头，例如 apiUrl('/api/boss/config') */
export function apiUrl(path: string): string {
  return `${API_BASE}${path}`
}
