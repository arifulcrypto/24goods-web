import { defineConfig } from 'vite'
import { noRemoteAssets } from '../../scripts/vite-no-remote-assets.mjs'
import vue from '@vitejs/plugin-vue'
import Components from 'unplugin-vue-components/vite'
import { VantResolver } from '@vant/auto-import-resolver'

const BACKEND = process.env.VITE_BACKEND ?? 'http://127.0.0.1:18000'

export default defineConfig({
  base: '/',
  plugins: [
    noRemoteAssets(),
    vue(),
    // Vant components are imported on demand (with their CSS) when used in templates.
    Components({
      resolvers: [VantResolver()],
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
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': { target: BACKEND, changeOrigin: false },
      '/mockpay': { target: process.env.VITE_MOCKPAY ?? BACKEND, changeOrigin: false },
    },
  },
  preview: { port: 4173 },
  build: {
    target: 'es2020',
    manifest: true,
    sourcemap: false,
    cssCodeSplit: true,
    reportCompressedSize: false,
    chunkSizeWarningLimit: 300,
  },
})
