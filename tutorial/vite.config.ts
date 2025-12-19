import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'

const __dirname = dirname(fileURLToPath(import.meta.url))

export default defineConfig({
  build: {
    outDir: "../ankihub/gui/web/lib/",
    lib: {
      entry: resolve(__dirname, 'lib/main.js'),
      name: 'AnkiHub',
      fileName: () => "tutorial.js",
      cssFileName: "tutorial",
      formats: ["umd"],
    },
  },
})
