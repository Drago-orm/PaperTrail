import { defineConfig } from 'vite'

const backendPort = Number(process.env.PAPERTRAIL_API_PORT ?? '8000')
if (!Number.isInteger(backendPort) || backendPort < 1024 || backendPort > 65535) {
  throw new Error('PAPERTRAIL_API_PORT 必须为 1024—65535 的本机端口')
}

export default defineConfig({
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    cors: false,
    proxy: {
      '/api': `http://127.0.0.1:${backendPort}`,
    },
  },
})
