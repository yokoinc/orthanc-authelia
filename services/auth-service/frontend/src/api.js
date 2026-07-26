// Wrapper fetch pour tous les appels /api/admin/*. Injecte automatiquement
// le header X-CSRF-Token depuis le cookie orthanc_admin_csrf pose par le
// backend au rendu initial de la page admin.

import { t } from './i18n.js'

function csrfToken() {
  const match = document.cookie
    .split('; ')
    .find((c) => c.startsWith('orthanc_admin_csrf='))
  return match ? match.split('=')[1] : ''
}

export async function api(path, opts = {}) {
  const headers = {
    'content-type': 'application/json',
    'x-csrf-token': csrfToken(),
    ...(opts.headers || {}),
  }
  const body =
    opts.body && typeof opts.body !== 'string'
      ? JSON.stringify(opts.body)
      : opts.body

  let r
  try {
    r = await fetch(path, {
      ...opts,
      headers,
      body,
      credentials: 'same-origin',
    })
  } catch (e) {
    // fetch ne rejette que sur echec reseau : serveur injoignable, certificat
    // refuse, redirection vers une autre origine bloquee par CORS. Le message
    // natif ("Failed to fetch") n'aide personne.
    throw new Error(
      t(
        'api_unreachable',
        "Impossible de joindre le serveur ({path}). Vérifier que la pile tourne et que le certificat de {origin} est accepté par le navigateur. Détail technique : {detail}",
        { path, origin: window.location.origin, detail: e.message },
      ),
    )
  }

  const text = await r.text()
  let data
  try {
    data = text ? JSON.parse(text) : {}
  } catch {
    // Reponse non-JSON : page HTML d'Authelia ou d'erreur nginx. Signaler la
    // cause probable plutot que de recracher le HTML brut.
    if (r.status === 401 || r.status === 403) {
      throw new Error(
        t(
          'api_session_expired',
          'Session expirée ou droits insuffisants. Se reconnecter, et vérifier que le compte appartient au groupe « admin ».',
        ),
      )
    }
    throw new Error(
      t(
        'api_unexpected_response',
        "Réponse inattendue du serveur (HTTP {status}, contenu non JSON). L'URL {path} a probablement été interceptée par une redirection.",
        { status: r.status, path },
      ),
    )
  }

  if (!r.ok) {
    const detail = data.detail || data.message
    throw new Error(
      detail || t('api_http_error', 'Le serveur a répondu HTTP {status}.', { status: r.status }),
    )
  }
  return data
}
