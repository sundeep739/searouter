import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    // The app needs a live backend, so offline precaching adds no value and
    // previously trapped devices on a stale bundle. selfDestroying ships a
    // service worker that unregisters any existing one and clears its caches,
    // so every device that had the old SW recovers automatically on next load.
    VitePWA({
      selfDestroying: true,
      registerType: 'autoUpdate',
      manifest: {
        name: 'SeaRouter',
        short_name: 'SeaRouter',
        description: 'Real navigable sea-route distances, voyages and distance verification',
        theme_color: '#0b1526',
        background_color: '#0b1526',
        display: 'standalone',
        icons: [
          { src: 'icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: 'icon-512.png', sizes: '512x512', type: 'image/png' },
        ],
      },
    }),
  ],
})
