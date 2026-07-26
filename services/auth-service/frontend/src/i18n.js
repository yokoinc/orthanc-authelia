// Libellés de l'interface.
//
// LANGUAGE est une variable d'environnement lue au démarrage du service :
// les libellés ne peuvent pas être figés dans le bundle au moment du build.
// auth_service.py les injecte donc dans index.html, sous window.__I18N__.
//
// Pas de dépendance i18n externe : le besoin se limite à une table de
// correspondance et une interpolation, et le fichier de traductions existait
// déjà côté serveur (translations/{en,fr}.json).

const source = window.__I18N__ || { lang: 'en', ui: {} }

export const lang = source.lang || 'en'

/**
 * Traduit une clé.
 *
 * @param cle    clé dans la section "ui" des fichiers de traduction
 * @param repli  texte affiché si la clé manque — toujours le fournir en
 *               français lisible : une clé absente doit dégrader vers une
 *               phrase compréhensible, jamais vers un identifiant technique
 *               ni vers du vide au milieu de l'écran.
 * @param vars   valeurs interpolées, référencées par {nom} dans le libellé
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
