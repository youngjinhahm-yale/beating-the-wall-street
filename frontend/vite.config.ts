import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Production: FastAPI serves dist/ and the API from one origin.
// Development: `npm run dev` proxies /api to the backend on port 8000.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
})
