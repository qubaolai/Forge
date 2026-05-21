import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000', // Python 后端地址
        changeOrigin: true,
        // SSE 必须不缓冲
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
            // 防止代理层缓冲 SSE
            delete proxyRes.headers['content-length'];
          });
        },
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    rollupOptions: {
      output: {
        manualChunks: {
          react: ['react', 'react-dom', 'react-router-dom'],
          query: ['@tanstack/react-query'],
          markdown: ['react-markdown', 'remark-gfm', 'rehype-highlight', 'rehype-katex'],
        },
      },
    },
  },
});
