import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { App } from '@/App'
import { applyTheme, storedTheme } from '@/lib/theme'

import './index.css'

// Before the first render, so the console never flashes the wrong theme.
// `index.html` ships `class="dark"` already; this restores a viewer who chose
// light last time.
applyTheme(storedTheme())

const container = document.getElementById('root')
if (!container) throw new Error('missing #root element in index.html')

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
