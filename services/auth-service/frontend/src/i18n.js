// Interface labels.
//
// The language is a setting changed from the panel, so labels cannot be
// frozen into the bundle at build time. auth_service.py therefore injects
// them into index.html, under window.__I18N__.
//
// No external i18n dependency: the need amounts to a lookup table and some
// interpolation, and the translation files already existed on the server
// side (translations/{en,fr}.json).

const source = window.__I18N__ || { lang: 'en', ui: {} }

export const lang = source.lang || 'en'

/**
 * Translate a key.
 *
 * @param cle    key in the "ui" section of the translation files
 * @param repli  text shown when the key is missing -- always provide it as
 *               readable French: a missing key must degrade to an
 *               understandable sentence, never to a technical identifier nor
 *               to a blank in the middle of the screen.
 * @param vars   interpolated values, referenced as {name} in the label
 */
export function t(key, fallback, vars) {
  let text = source.ui[key]
  if (text === undefined) {
    if (import.meta.env.DEV) console.warn(`[i18n] missing key: ${key}`)
    text = fallback !== undefined ? fallback : key
  }
  if (vars) {
    for (const [name, value] of Object.entries(vars)) {
      text = text.replaceAll(`{${name}}`, String(value))
    }
  }
  return text
}
