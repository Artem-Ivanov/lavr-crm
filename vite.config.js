import { defineConfig, loadEnv } from 'vite';
import vue from '@vitejs/plugin-vue';

export default defineConfig(({ mode }) => ({
  plugins: [vue()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: { '/api': { target: loadEnv(mode, process.cwd(), 'API_PROXY_TARGET').API_PROXY_TARGET || 'http://127.0.0.1:8000' } },
    fs: {
      deny: ['.env', '.env.*', '*.{crt,pem}', '**/.git/**', '**/.agents/**', '**/.codex/**', '**/.aws/**', '**/.planning/**'],
    },
  },
}));
