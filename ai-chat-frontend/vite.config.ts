import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  base: './',  // Capacitor 用 file:// 协议加载，绝对路径 /assets/ 会解析到文件系统根目录
  build: {
    // 部分 Android 设备仍可能使用 Chrome 103 WebView。若不声明兼容目标，
    // Vite 8 会把 max-width 媒体查询压成旧 WebView 无法解析的范围语法，
    // 导致整层手机布局被当作 `@media not all` 丢弃。
    cssTarget: 'chrome103',
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
    strictPort: true,  // 5173 被占直接报错，不静默切 5174/5175
  },
})
