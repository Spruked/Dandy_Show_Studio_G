import { defineConfig } from 'vite'

export default defineConfig({
  server: {
    // Rust/Cargo writes locked executables under this directory during Tauri dev.
    // Vite must not watch it on Windows.
    watch: {
      ignored: ['**/src-tauri/target/**'],
    },
  },
})
