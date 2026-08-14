// La consola habla con /v1 (plano protegido): siempre con token y rol.
const BASE = import.meta.env.VITE_API_BASE ?? '/colombia-unida/api'

const TOKEN_KEY = 'cu.console.token'
const USER_KEY = 'cu.console.user'

export const getToken = () => sessionStorage.getItem(TOKEN_KEY)
export const getStoredUser = () => {
  try {
    return JSON.parse(sessionStorage.getItem(USER_KEY) ?? 'null')
  } catch {
    return null
  }
}

export function logout() {
  // Sesión en sessionStorage, no localStorage: se cierra al cerrar la
  // pestaña, coherente con "sesiones cortas" del alcance (§13.2).
  sessionStorage.removeItem(TOKEN_KEY)
  sessionStorage.removeItem(USER_KEY)
}

export class AuthError extends Error {}

async function request(path, options = {}) {
  const token = getToken()
  const res = await fetch(`${BASE}${path}`, {
    ...options,
    headers: {
      Accept: 'application/json',
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers ?? {}),
    },
  })
  if (res.status === 401) {
    logout()
    throw new AuthError('Sesión expirada')
  }
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      detail = body.detail ?? body.title ?? detail
    } catch {
      /* respuesta sin cuerpo JSON */
    }
    throw new Error(detail)
  }
  return res.json()
}

export async function login(email, password) {
  const res = await fetch(`${BASE}/v1/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail ?? 'No pudimos iniciar sesión')
  }
  const data = await res.json()
  sessionStorage.setItem(TOKEN_KEY, data.access_token)
  sessionStorage.setItem(USER_KEY, JSON.stringify(data.user))
  return data.user
}

export const fetchOverview = () => request('/v1/console/overview')
export const fetchCases = (params = {}) => request(`/v1/console/cases${qs(params)}`)
export const fetchCase = (id) => request(`/v1/console/cases/${id}`)
export const fetchNeeds = (params = {}) => request(`/v1/console/needs${qs(params)}`)
export const fetchResources = () => request('/v1/console/resources')
export const fetchMatching = () => request('/v1/console/matching')
export const fetchAllocations = () => request('/v1/console/allocations')

// Acciones sobre el matching. Van contra /v1 (no /v1/console): son las
// mismas operaciones de dominio que expone la API, con su máquina de
// estados y su reserva atómica.
export function reserveAllocation(matchId, quantity) {
  return request('/v1/allocations', {
    method: 'POST',
    // La Idempotency-Key se deriva del match y la cantidad: si el
    // operador da doble clic, el segundo request devuelve la misma
    // reserva en vez de duplicarla (§10.3).
    headers: { 'Idempotency-Key': `console-${matchId}-${quantity}` },
    body: JSON.stringify({ match_id: matchId, quantity }),
  })
}

export const rejectMatch = (matchId, reason) =>
  request(`/v1/matches/${matchId}:reject`, {
    method: 'POST',
    body: JSON.stringify({ reason }),
  })

function qs(params) {
  const entries = Object.entries(params).filter(([, v]) => v != null && v !== '')
  return entries.length ? `?${new URLSearchParams(entries)}` : ''
}
