/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// Tests run in a non-UTC zone on purpose: a timestamp parsed as local time must fail
// everywhere, not only on developers' machines that are not on UTC.
process.env.TZ = 'Asia/Kolkata';

// The dashboard only ever talks to /api. In dev and `vite preview` that path is
// proxied to the FastAPI server; in compose, nginx does the same. No CORS needed.
const API = process.env.API_URL ?? 'http://localhost:8000';
const proxy = { '/api': { target: API, rewrite: (p: string) => p.replace(/^\/api/, '') } };

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy },
  preview: { port: 4173, proxy },
  test: {
    environment: 'jsdom', include: ['src/**/*.test.{ts,tsx}'], restoreMocks: true,
    setupFiles: ['src/test-setup.ts'],
  },
});
