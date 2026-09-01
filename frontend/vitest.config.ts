/**
 * Test configuration.
 *
 * Separate from `vite.config.ts` on purpose: that file refuses a production
 * build when `VITE_NO_AUTH` is set, and it splits vendor chunks — neither of
 * which has any meaning under a test runner, and the bypass guard would make
 * the suite fail on a developer's machine for a reason unrelated to the tests.
 */

import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    // Only what we write. `dist` and `node_modules` contain compiled copies of
    // the same files and would run each test twice.
    include: ['src/**/*.test.{ts,tsx}'],
  },
})
