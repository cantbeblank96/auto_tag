import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const webRoot = path.dirname(fileURLToPath(import.meta.url))
const constantPy = path.resolve(webRoot, '../constant.py')

/** 与 auto_tag/constant.py 中 VERSION 保持同步（构建/ dev 启动时读取）。 */
function readAppVersion(): string {
  const text = fs.readFileSync(constantPy, 'utf-8')
  const match = text.match(/^VERSION\s*=\s*["']([^"']+)["']/m)
  if (!match) {
    throw new Error(`VERSION not found in ${constantPy}`)
  }
  return match[1]
}

const appVersion = readAppVersion()

export default defineConfig({
  plugins: [react(), tailwindcss()],
  define: {
    __APP_VERSION__: JSON.stringify(appVersion),
  },
  server: {
    port: 5020,
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})
