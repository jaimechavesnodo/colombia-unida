// El plano público consume exclusivamente /public/v1/* (§6.3, §14.2).
// Mismo-origen siempre: en dev vía proxy de Vite, en producción vía nginx.
const BASE = import.meta.env.VITE_API_BASE ?? '/colombia-unida/api'

async function get(path) {
  const res = await fetch(`${BASE}/public/v1${path}`, {
    headers: { Accept: 'application/json' },
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

export const fetchImpact = () => get('/impact')
export const fetchFeed = (params = {}) => {
  const qs = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v != null && v !== ''),
  ).toString()
  return get(`/feed${qs ? `?${qs}` : ''}`)
}

export const fetchCase = (slug) => get(`/cases/${encodeURIComponent(slug)}`)
export const fetchHelpOptions = (slug) => get(`/cases/${encodeURIComponent(slug)}/help-options`)

/** Registra un ofrecimiento. El error del servidor se propaga con su texto:
 *  son mensajes escritos para que los lea quien está llenando el formulario. */
export async function submitHelpOffer(payload) {
  const res = await fetch(`${BASE}/public/v1/help-offers`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  const body = await res.json().catch(() => ({}))
  if (!res.ok) {
    const detail = body.detail
    throw new Error(
      typeof detail === 'string'
        ? detail
        : // 422 de Pydantic: llega una lista de errores por campo.
          (detail?.[0]?.msg ?? 'No pudimos registrar tu ofrecimiento'),
    )
  }
  return body
}

export async function reportContent(slug, reasonCode) {
  const res = await fetch(`${BASE}/public/v1/content-reports`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ slug, reason_code: reasonCode }),
  })
  return res.ok
}
