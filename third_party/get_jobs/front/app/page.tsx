'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'

/**
 * 根路径跳转到环境配置页。
 *
 * 注意：这里不能用 next/navigation 的服务端 redirect()。
 * 本项目配了 output: 'export'（纯静态导出），服务端重定向没有运行时可执行，
 * Next 会把首页导出成一个 __next_error__ 错误壳（只有 5KB、没有内容），
 * 打开 http://localhost:6866/ 就是一片空白。
 * 静态导出下只能走客户端跳转，另外配一个 meta refresh 兜住禁用 JS 的情况。
 */
export default function HomeRedirect() {
  const router = useRouter()

  useEffect(() => {
    router.replace('/env-config')
  }, [router])

  return (
    <>
      <meta httpEquiv="refresh" content="0; url=/env-config" />
      <noscript>
        <a href="/env-config">进入管理页面</a>
      </noscript>
    </>
  )
}
