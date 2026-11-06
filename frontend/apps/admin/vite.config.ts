import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'

const BACKEND = process.env.VITE_BACKEND ?? 'http://127.0.0.1:18000'

export default defineConfig({
  base: '/admin/',
  plugins: [
    vue(),
    // ElMessage / ElMessageBox / ElNotification (+ their CSS) on demand
    AutoImport({
      resolvers: [ElementPlusResolver({ importStyle: 'css' })],
      dts: 'src/auto-imports.d.ts',
    }),
    // <el-*> components (+ their CSS) on demand
    Components({
      resolvers: [ElementPlusResolver({ importStyle: 'css' })],
      dts: 'src/components.d.ts',
      dirs: [],
    }),
  ],
  define: {
    __VUE_I18N_FULL_INSTALL__: true,
    __VUE_I18N_LEGACY_API__: false,
    __INTLIFY_PROD_DEVTOOLS__: false,
    __VUE_PROD_DEVTOOLS__: false,
    __VUE_PROD_HYDRATION_MISMATCH_DETAILS__: false,
  },
  server: {
    port: 5174,
    strictPort: true,
    proxy: {
      '/api': { target: BACKEND, changeOrigin: false },
      '/mockpay': { target: process.env.VITE_MOCKPAY ?? BACKEND, changeOrigin: false },
    },
  },
  preview: { port: 4174 },
  build: {
    target: 'es2020',
    manifest: true,
    sourcemap: false,
    reportCompressedSize: false,
    chunkSizeWarningLimit: 800,
  },
})
