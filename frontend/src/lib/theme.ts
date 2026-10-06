/**
 * Light/dark, persisted per viewer.
 *
 * Dark is the default and `index.html` ships `class="dark"` on <html> so the
 * console never flashes white on load: a SOC screen is read for a whole shift
 * in a dim room. Light is a real alternative rather than a courtesy -- a team
 * lead reading the analytics screen by a window is the case it exists for, and
 * every token is defined for both.
 */
const STORAGE_KEY = 'recluse.theme'

export type Theme = 'dark' | 'light'

/** Whatever the viewer last chose, or dark. */
export function storedTheme(): Theme {
  try {
    return localStorage.getItem(STORAGE_KEY) === 'light' ? 'light' : 'dark'
  } catch {
    // Private windows and blocked site data both throw here. A console that
    // refused to render because it could not remember a colour preference
    // would be a worse failure than forgetting one.
    return 'dark'
  }
}

export function applyTheme(theme: Theme): void {
  document.documentElement.classList.toggle('dark', theme === 'dark')
  document.documentElement.style.colorScheme = theme
  try {
    localStorage.setItem(STORAGE_KEY, theme)
  } catch {
    /* see storedTheme */
  }
}
