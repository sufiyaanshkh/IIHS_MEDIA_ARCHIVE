import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
const apiTarget = process.env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8010';
export default defineConfig({ plugins: [react()], server: { proxy: { '/api': apiTarget, '/healthz': apiTarget, '/readyz': apiTarget } } });
