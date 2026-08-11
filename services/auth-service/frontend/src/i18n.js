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
export function t(cle, repli, vars) {
  let texte = source.ui[cle]
  if (texte === undefined) {
    if (import.meta.env.DEV) console.warn(`[i18n] clé absente : ${cle}`)
    texte = repli !== undefined ? repli : cle
  }
  if (vars) {
    for (const [nom, valeur] of Object.entries(vars)) {
      texte = texte.replaceAll(`{${nom}}`, String(valeur))
    }
  }
  return texte
}
