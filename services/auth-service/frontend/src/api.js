// Wrapper fetch pour tous les appels /api/admin/*. Injecte automatiquement
// le header X-CSRF-Token depuis le cookie orthanc_admin_csrf pose par le
// backend au rendu initial de la page admin.

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
      `Impossible de joindre le serveur (${path}). Vérifie que la stack tourne ` +
      `et que le certificat de ${window.location.origin} est accepté par le navigateur. ` +
      `Détail technique : ${e.message}`,
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
        'Session expirée ou droits insuffisants. Reconnecte-toi, ' +
        'et vérifie que ton compte appartient au groupe "admins".',
      )
    }
    throw new Error(
      `Réponse inattendue du serveur (HTTP ${r.status}, contenu non-JSON). ` +
      `L'URL ${path} a probablement été interceptée par une redirection.`,
    )
  }

  if (!r.ok) {
    const detail = data.detail || data.message
    throw new Error(detail || `Le serveur a répondu HTTP ${r.status}.`)
  }
  return data
}
