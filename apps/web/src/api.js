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

export async function reportContent(slug, reasonCode) {
  const res = await fetch(`${BASE}/public/v1/content-reports`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ slug, reason_code: reasonCode }),
  })
  return res.ok
}
