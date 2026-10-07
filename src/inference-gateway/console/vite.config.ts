import { defineConfig } from 'vite';

export default defineConfig({
  base: '/console/',
  build: { outDir: '../app/console', emptyOutDir: true },
  server: { proxy: { '/v1': 'http://127.0.0.1:8080' } },
});
