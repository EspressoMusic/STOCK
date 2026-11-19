import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // The chart panel goes to the live site too (19/11/26), so the phone opens the user's trades;
  // its candles and prices come from api/*.js there.
  build: {
    rollupOptions: {
      input: { main: 'index.html', tv: 'tv.html' },
    },
  },
  server: {
    host: true,
    port: 5174,
    strictPort: true,
    // The phone opens the app over https through Tailscale (`tailscale serve --bg 5174`),
    // at this computer's *.ts.net address; the API calls go on to the backend from here.
    allowedHosts: ['.ts.net'],
    proxy: {
      '/api': 'http://127.0.0.1:8001',
    },
  },
})
