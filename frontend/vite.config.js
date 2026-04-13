import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// Read env so we can use VITE_BACKEND_PORT at config time (not just in browser code).
// loadEnv reads from .env, .env.local, etc. in the project root.
// start.sh writes VITE_BACKEND_PORT to frontend/.env.local after the backend
// starts, so this proxy always targets the actual bound port (even if 8010 was
// busy and the backend incremented to 8011, 8012, etc.).
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')          // '' prefix = all vars
  const backendPort = env.VITE_BACKEND_PORT || '8010'   // default 8010
  const backendTarget = `http://localhost:${backendPort}`

  return {
    plugins: [react()],
    server: {
      port: 5174,
      proxy: {
        '/api': {
          target: backendTarget,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, ''),
        },
      },
    },
  }
})