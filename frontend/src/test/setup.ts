/**
 * Test setup.
 *
 * `jest-dom` adds the DOM matchers (`toBeInTheDocument` and friends), and each
 * test starts from a clean localStorage — the refresh token is persisted
 * there, so a leftover value from one test signs the next one in.
 */

import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, beforeEach } from 'vitest'

beforeEach(() => {
  localStorage.clear()
})

afterEach(() => {
  cleanup()
})
