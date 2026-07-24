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

  const r = await fetch(path, {
    ...opts,
    headers,
    body,
    credentials: 'same-origin',
  })

  const text = await r.text()
  let data
  try {
    data = text ? JSON.parse(text) : {}
  } catch {
    data = { detail: text }
  }
  if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`)
  return data
}
