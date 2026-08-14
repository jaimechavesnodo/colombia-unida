import { useCallback, useEffect, useState } from 'react'
import {
  AuthError,
  fetchAllocations,
  fetchCase,
  fetchCases,
  fetchMatching,
  fetchNeeds,
  fetchOverview,
  fetchResources,
  getStoredUser,
  getToken,
  login,
  logout,
  rejectMatch,
  reserveAllocation,
} from './api.js'
import {
  IconArrow,
  IconCheck,
  IconClock,
  IconDelivered,
  IconDot,
  IconHeart,
  IconInbox,
  IconShield,
  IconVerified,
} from './icons.jsx'
import './styles.css'
import './console.css'

const IMG = `${import.meta.env.BASE_URL}img`

const CASE_STATUS_ES = {
  DRAFT: 'En registro',
  INCOMPLETE: 'Faltan datos',
  PENDING_VERIFICATION: 'Por verificar',
  VERIFIED: 'Verificado',
  ACTIVE: 'Activo',
  PARTIALLY_SERVED: 'Ayuda parcial',
  SERVED: 'Ayuda entregada',
  CLOSED: 'Cerrado',
  ON_HOLD: 'En pausa',
  DUPLICATE: 'Duplicado',
  REJECTED: 'Rechazado',
  CANCELLED: 'Cancelado',
  SUSPICIOUS: 'En revisión',
}

const NEED_STATUS_ES = {
  REPORTED: 'Reportada',
  NEEDS_CLARIFICATION: 'Requiere aclaración',
  PENDING_VERIFICATION: 'Por verificar',
  VERIFIED: 'Verificada',
  PUBLICABLE: 'Publicable',
  OPEN: 'Abierta',
  PARTIALLY_COVERED: 'Cubierta parcial',
  COVERED: 'Cubierta',
  IN_TRANSIT: 'En tránsito',
  DELIVERED_PENDING_VERIFY: 'Entregada, por verificar',
  DELIVERED_VERIFIED: 'Entrega verificada',
  CLOSED: 'Cerrada',
}

const HORIZON_ES = {
  EMERGENCY: 'Emergencia',
  RECOVERY: 'Recuperación',
  RECONSTRUCTION: 'Reconstrucción',
}

const OFFER_TYPE_ES = {
  MONEY: 'Dinero',
  IN_KIND: 'Especie',
  SERVICE: 'Servicio',
  TRANSPORT: 'Transporte',
  VOLUNTEERING: 'Voluntariado',
}

const OFFER_STATUS_ES = {
  DRAFT: 'Borrador',
  PENDING_CONFIRMATION: 'Por confirmar',
  AVAILABLE: 'Disponible',
  PARTIALLY_ALLOCATED: 'Asignada en parte',
  FULLY_ALLOCATED: 'Asignada por completo',
  EXPIRED: 'Vencida',
  WITHDRAWN: 'Retirada',
  BLOCKED: 'Bloqueada',
}

const ALLOCATION_STATUS_ES = {
  DRAFT: 'Borrador',
  RESERVED: 'Reservada',
  DONOR_CONFIRMED: 'Confirmada por donante',
  ACCEPTED_BY_OPERATOR: 'Aceptada por operación',
  READY_FOR_FULFILLMENT: 'Lista para despacho',
  IN_FULFILLMENT: 'En despacho',
  FULFILLED: 'Entregada',
  PARTIALLY_FULFILLED: 'Entregada en parte',
  EXPIRED: 'Vencida',
  CANCELLED: 'Cancelada',
  DISPUTED: 'En disputa',
}

const CONDITION_ES = {
  NEW: 'Nuevo',
  GOOD: 'Buen estado',
  USED: 'Usado',
  REFURBISHED: 'Reacondicionado',
  NOT_APPLICABLE: 'No aplica',
}

const TRUST_REVIEW_ES = {
  NONE: 'Sin señales de riesgo',
  PENDING: 'Revisión pendiente',
  IN_REVIEW: 'En revisión',
  CLEARED: 'Revisada y despejada',
  ESCALATED: 'Escalada',
}

// Plural en español: la consola nunca debe mostrar el enum crudo en inglés.
const PERSON_ROLE_ES = {
  AFFECTED: ['persona afectada', 'personas afectadas'],
  REPORTER: ['quien reporta', 'quienes reportan'],
  CAREGIVER: ['cuidador', 'cuidadores'],
  BENEFICIARY_CONTACT: ['contacto', 'contactos'],
  COMMUNITY_LEADER: ['líder comunitario', 'líderes comunitarios'],
  RECEIVER: ['quien recibe', 'quienes reciben'],
  WITNESS: ['testigo', 'testigos'],
}

// Tono del estado: no se apoya solo en color (regla #10) — el texto manda.
const STATUS_TONE = {
  INCOMPLETE: 'warn',
  PENDING_VERIFICATION: 'warn',
  NEEDS_CLARIFICATION: 'warn',
  SUSPICIOUS: 'danger',
  REJECTED: 'danger',
  CANCELLED: 'muted',
  DUPLICATE: 'muted',
  CLOSED: 'muted',
  VERIFIED: 'info',
  PUBLICABLE: 'info',
  ACTIVE: 'info',
  OPEN: 'info',
  PARTIALLY_COVERED: 'ok',
  COVERED: 'ok',
  PARTIALLY_SERVED: 'ok',
  SERVED: 'ok',
  DELIVERED_VERIFIED: 'ok',
}

const titleCase = (s) =>
  (s ?? '').toLowerCase().replace(/(^|\s|-)([a-záéíóúñ])/g, (_, p, c) => p + c.toUpperCase())

// unit_code del catálogo viene en clave técnica; la consola lo dice en español
// y concuerda en número (1 kit / 3 kits).
const UNIT_ES = {
  UNIT: ['unidad', 'unidades'],
  KIT: ['kit', 'kits'],
  SERVICE: ['servicio', 'servicios'],
  TRIP: ['viaje', 'viajes'],
  PERSON_DAY: ['día-persona', 'días-persona'],
  L: ['L', 'L'],
  M2: ['m²', 'm²'],
}

const fmtQty = (n, unit) => {
  if (n == null) return '—'
  const qty = Number(n)
  const label = UNIT_ES[unit] ?? (unit ? [unit.toLowerCase(), unit.toLowerCase()] : null)
  const text = qty.toLocaleString('es-CO', { maximumFractionDigits: 1 })
  return label ? `${text} ${qty === 1 ? label[0] : label[1]}` : text
}

const fmtDate = (d) =>
  d
    ? new Date(d).toLocaleDateString('es-CO', {
        day: '2-digit',
        month: 'short',
        hour: '2-digit',
        minute: '2-digit',
      })
    : '—'

const daysSince = (d) => (d ? Math.floor((Date.now() - new Date(d)) / 86400000) : null)

function Pill({ children, tone = 'neutral' }) {
  return <span className={`pill pill-${tone}`}>{children}</span>
}

function StatusPill({ status, dictionary = CASE_STATUS_ES }) {
  return <Pill tone={STATUS_TONE[status] ?? 'neutral'}>{dictionary[status] ?? status}</Pill>
}

function Stat({ icon: Icon, tint, label, value, note }) {
  return (
    <div className="kpi glass">
      <div className="kpi-head">
        <span className="kpi-icon" style={{ color: tint }}>
          <Icon />
        </span>
        <p className="kpi-label sans">{label}</p>
      </div>
      <p className="kpi-value">{value}</p>
      {note && <p className="kpi-note sans">{note}</p>}
    </div>
  )
}

/** Barra de cobertura: siempre acompañada del número, nunca solo color. */
function Coverage({ pct }) {
  const v = Math.round(pct ?? 0)
  return (
    <div className="coverage">
      <div className="coverage-track" role="img" aria-label={`Cobertura ${v}%`}>
        <div
          className={`coverage-fill${v < 34 ? ' low' : ''}`}
          style={{ width: `${Math.max(v, 2)}%` }}
        />
      </div>
      <span className="coverage-value sans">{v}%</span>
    </div>
  )
}

// ── Login ──────────────────────────────────────────────────────────────

function Login({ onDone }) {
  const [email, setEmail] = useState('supervisor@colombiaunida.demo')
  const [password, setPassword] = useState('Demo1234!')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function submit(e) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      onDone(await login(email, password))
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-wrap">
      <form className="login glass" onSubmit={submit}>
        <p className="login-eyebrow sans">Consola de operación</p>
        <h1 className="login-title">
          Colombia <span>Unida</span>
        </h1>
        <p className="login-lede sans">
          Acceso restringido al equipo humanitario. Cada consulta a datos protegidos queda
          registrada.
        </p>

        <label className="field sans">
          <span>Correo</span>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="username"
            required
          />
        </label>
        <label className="field sans">
          <span>Contraseña</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
        </label>

        {error && (
          <p className="field-error sans" role="alert">
            {error}
          </p>
        )}

        <button className="btn" type="submit" disabled={busy}>
          {busy ? 'Verificando…' : 'Entrar'}
        </button>

        <p className="login-demo sans">
          Demo: <code>supervisor@colombiaunida.demo</code> · <code>Demo1234!</code>
          <br />
          También <code>agente@</code> y <code>validador@</code> para ver los permisos por rol.
        </p>
      </form>
    </div>
  )
}

// ── Command Center ─────────────────────────────────────────────────────

function Overview({ data, onGo }) {
  const c = data.cases
  const n = data.needs
  const s = data.supply

  return (
    <>
      <div className="kpi-grid">
        <Stat
          icon={IconInbox}
          tint="var(--sky)"
          label="Casos reportados"
          value={c.total}
          note={`${c.needs_attention} requieren atención ahora`}
        />
        <Stat
          icon={IconVerified}
          tint="var(--amber)"
          label="Casos activos"
          value={c.active}
          note="Verificados y en gestión de ayuda"
        />
        <Stat
          icon={IconHeart}
          tint="var(--sky)"
          label="Cobertura de necesidades"
          value={`${n.coverage_pct}%`}
          note={`${fmtQty(n.covered_qty)} cubierto de ${fmtQty(n.requested_qty)}`}
        />
        <Stat
          icon={IconDelivered}
          tint="var(--amber)"
          label="Reservas vigentes"
          value={s.allocations_reserved}
          note={`${s.matches_pending} ${
            s.matches_pending === 1 ? 'candidato' : 'candidatos'
          } por aprobar`}
        />
      </div>

      <div className="two-col">
        <section>
          <h2 className="section-title">Casos por estado</h2>
          <p className="section-sub sans">
            Lo que está arriba es lo que espera una decisión humana.
          </p>
          <div className="panel glass">
            {Object.entries(c.by_status)
              .sort((a, b) => b[1] - a[1])
              .map(([status, count]) => (
                <button
                  key={status}
                  className="row-link sans"
                  type="button"
                  onClick={() => onGo('cases', status)}
                >
                  <StatusPill status={status} />
                  <span className="row-count">{count}</span>
                  <IconArrow />
                </button>
              ))}
          </div>
        </section>

        <section>
          <h2 className="section-title">Colas de trabajo</h2>
          <p className="section-sub sans">
            Conversaciones y casos esperando a una persona del equipo.
          </p>
          <div className="panel glass">
            {data.queues.length === 0 ? (
              <div className="row-static sans">
                <span className="muted">Ninguna cola con casos asignados</span>
                <span className="row-count">0</span>
              </div>
            ) : (
              data.queues.map((q) => (
                <div className="row-static sans" key={`${q.queue}-${q.priority}`}>
                  <span>
                    <strong>{q.queue}</strong>{' '}
                    <Pill
                      tone={
                        q.priority === 'P0' ? 'danger' : q.priority === 'P1' ? 'warn' : 'neutral'
                      }
                    >
                      {q.priority}
                    </Pill>
                  </span>
                  <span className="row-count">{q.count}</span>
                </div>
              ))
            )}
            <div className="row-static sans">
              <span>Conversaciones de WhatsApp con mensajes recibidos</span>
              <span className="row-count">{data.conversations_with_inbound}</span>
            </div>
            <div className="row-static sans">
              <span>Ofertas de donantes abiertas</span>
              <span className="row-count">{s.offers_open}</span>
            </div>
          </div>
        </section>
      </div>

      <h2 className="section-title">Necesidades por estado</h2>
      <p className="section-sub sans">
        Cada necesidad avanza por su cuenta: un caso puede tener una entregada y otra abierta.
      </p>
      <div className="panel glass chips-row">
        {Object.entries(n.by_status)
          .sort((a, b) => b[1] - a[1])
          .map(([status, count]) => (
            <button
              key={status}
              className="tag-btn sans"
              type="button"
              onClick={() => onGo('needs', status)}
            >
              <StatusPill status={status} dictionary={NEED_STATUS_ES} />
              <strong>{count}</strong>
            </button>
          ))}
      </div>
    </>
  )
}

// ── Casos ──────────────────────────────────────────────────────────────

function Cases({ filter, onOpenCase }) {
  const [status, setStatus] = useState(filter ?? '')
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    setData(null)
    fetchCases({ status, limit: 100 })
      .then(setData)
      .catch((e) => setError(e.message))
  }, [status])

  return (
    <>
      <div className="toolbar">
        <select
          className="chip sans"
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          aria-label="Filtrar por estado"
        >
          <option value="">Todos los estados</option>
          {Object.entries(CASE_STATUS_ES).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>
        <span className="filter-count sans">
          {data ? `${data.items.length} casos` : 'Cargando…'}
        </span>
      </div>

      {error && <p className="empty sans">{error}</p>}

      {data && (
        <div className="panel glass table-wrap">
          <table className="table sans">
            <thead>
              <tr>
                <th>Caso</th>
                <th>Estado</th>
                <th>Municipio</th>
                <th className="num">Hogar</th>
                <th className="num">Necesidades</th>
                <th>Registrado</th>
                <th aria-label="Abrir" />
              </tr>
            </thead>
            <tbody>
              {data.items.map((c) => (
                <tr key={c.id}>
                  <td>
                    <code>{c.case_code}</code>
                  </td>
                  <td>
                    <StatusPill status={c.status} />
                  </td>
                  <td>{titleCase(c.municipality) || '—'}</td>
                  <td className="num">{c.household_size ?? '—'}</td>
                  <td className="num">{c.needs_count}</td>
                  <td>
                    {fmtDate(c.opened_at)}
                    {daysSince(c.opened_at) != null && (
                      <span className="aging sans"> · {daysSince(c.opened_at)} d</span>
                    )}
                  </td>
                  <td>
                    <button
                      className="mini-btn sans"
                      type="button"
                      onClick={() => onOpenCase(c.id)}
                    >
                      Abrir
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {data.items.length === 0 && <p className="empty sans">Sin casos con este filtro.</p>}
        </div>
      )}
    </>
  )
}

function CaseDetail({ caseId, onClose }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    fetchCase(caseId)
      .then(setData)
      .catch((e) => setError(e.message))
  }, [caseId])

  return (
    <div className="drawer-backdrop" onClick={onClose} role="presentation">
      <aside
        className="drawer glass"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Detalle del caso"
      >
        <button className="drawer-close sans" type="button" onClick={onClose} aria-label="Cerrar">
          ✕
        </button>

        {error && <p className="empty sans">{error}</p>}
        {!data && !error && <p className="empty sans">Cargando…</p>}

        {data && (
          <>
            <p className="drawer-eyebrow sans">
              {titleCase(data.location?.admin2)} · {titleCase(data.location?.admin1)}
            </p>
            <h2 className="drawer-title">
              <code>{data.case_code}</code> <StatusPill status={data.status} />
            </h2>

            <dl className="facts sans">
              <div>
                <dt>Hogar</dt>
                <dd>
                  {data.household.size ?? '—'} personas
                  {data.household.minors ? ` · ${data.household.minors} menores` : ''}
                </dd>
              </div>
              <div>
                <dt>Registrado</dt>
                <dd>{fmtDate(data.opened_at)}</dd>
              </div>
              <div>
                <dt>Revisión de confianza</dt>
                <dd>{TRUST_REVIEW_ES[data.trust_review_state] ?? '—'}</dd>
              </div>
              <div>
                <dt>Personas vinculadas</dt>
                <dd>
                  {Object.entries(data.persons_by_role)
                    .map(([role, n]) => {
                      const forms = PERSON_ROLE_ES[role] ?? [role, role]
                      return `${n} ${n === 1 ? forms[0] : forms[1]}`
                    })
                    .join(', ') || '—'}
                </dd>
              </div>
            </dl>

            <p className="privacy-inline sans">
              <IconShield />
              La identidad, el teléfono y la ubicación exacta no se muestran aquí. Consultarlos
              requiere justificación y deja registro de acceso.
            </p>

            <h3 className="drawer-h3">Necesidades ({data.needs.length})</h3>
            <div className="need-list">
              {data.needs.map((n) => {
                const base = n.confirmed_qty || n.requested_qty
                const pct = base ? (n.covered_qty / base) * 100 : 0
                return (
                  <div className="need-item glass-thin" key={n.id}>
                    <div className="need-head">
                      <strong className="sans">
                        {n.catalog_name ?? n.description ?? 'Sin catálogo'}
                      </strong>
                      <StatusPill status={n.status} dictionary={NEED_STATUS_ES} />
                    </div>
                    <div className="need-meta sans">
                      <span>{HORIZON_ES[n.horizon] ?? n.horizon}</span>
                      <span>
                        {fmtQty(n.covered_qty, n.unit)} de {fmtQty(base, n.unit)}
                      </span>
                    </div>
                    <Coverage pct={pct} />
                  </div>
                )
              })}
              {data.needs.length === 0 && (
                <p className="empty sans">Sin necesidades registradas.</p>
              )}
            </div>

            {data.validations.length > 0 && (
              <>
                <h3 className="drawer-h3">Validaciones</h3>
                <ul className="timeline sans">
                  {data.validations.map((v, i) => (
                    <li key={i}>
                      <IconCheck />
                      <span>
                        <strong>{v.outcome}</strong> · {v.type} · {fmtDate(v.performed_at)}
                      </span>
                    </li>
                  ))}
                </ul>
              </>
            )}

            <h3 className="drawer-h3">Historial de estados</h3>
            <ul className="timeline sans">
              {data.history.map((h, i) => (
                <li key={i}>
                  <IconDot />
                  <span>
                    {CASE_STATUS_ES[h.from] ?? h.from ?? 'inicio'} →{' '}
                    <strong>{CASE_STATUS_ES[h.to] ?? h.to}</strong>
                    <br />
                    <span className="muted">
                      {fmtDate(h.changed_at)}
                      {h.reason_code ? ` · ${h.reason_code}` : ''}
                    </span>
                  </span>
                </li>
              ))}
              {data.history.length === 0 && <p className="empty sans">Sin cambios registrados.</p>}
            </ul>
          </>
        )}
      </aside>
    </div>
  )
}

// ── Necesidades ────────────────────────────────────────────────────────

function Needs({ filter }) {
  const [status, setStatus] = useState(filter ?? '')
  const [data, setData] = useState(null)

  useEffect(() => {
    setData(null)
    fetchNeeds({ status, limit: 200 })
      .then(setData)
      .catch(() => setData({ items: [] }))
  }, [status])

  return (
    <>
      <div className="toolbar">
        <select
          className="chip sans"
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          aria-label="Filtrar por estado"
        >
          <option value="">Todos los estados</option>
          {Object.entries(NEED_STATUS_ES).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>
        <span className="filter-count sans">
          {data ? `${data.items.length} necesidades` : 'Cargando…'}
        </span>
      </div>

      {data && (
        <div className="panel glass table-wrap">
          <table className="table sans">
            <thead>
              <tr>
                <th>Necesidad</th>
                <th>Caso</th>
                <th>Municipio</th>
                <th>Horizonte</th>
                <th>Estado</th>
                <th className="num">Brecha</th>
                <th>Cobertura</th>
                <th className="num">Días</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((n) => (
                <tr key={n.id}>
                  <td>{n.catalog_name ?? '—'}</td>
                  <td>
                    <code>{n.case_code}</code>
                  </td>
                  <td>{titleCase(n.municipality) || '—'}</td>
                  <td>{HORIZON_ES[n.horizon] ?? n.horizon}</td>
                  <td>
                    <StatusPill status={n.status} dictionary={NEED_STATUS_ES} />
                  </td>
                  <td className="num">{fmtQty(n.gap_qty, n.unit)}</td>
                  <td className="cov-cell">
                    <Coverage pct={n.coverage_pct} />
                  </td>
                  <td className="num">{daysSince(n.created_at) ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {data.items.length === 0 && (
            <p className="empty sans">Sin necesidades con este filtro.</p>
          )}
        </div>
      )}
    </>
  )
}

// ── Inventario de recursos ─────────────────────────────────────────────

function Resources() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    fetchResources()
      .then(setData)
      .catch((e) => setError(e.message))
  }, [])

  if (error) return <p className="empty sans">{error}</p>
  if (!data) return <p className="empty sans">Cargando inventario…</p>

  const deficit = data.by_category.filter((c) => c.balance_qty < 0)

  return (
    <>
      <h2 className="section-title">Balance por categoría</h2>
      <p className="section-sub sans">
        Lo disponible contra la demanda abierta. Un balance negativo es una brecha que hoy no tiene
        con qué cubrirse.
      </p>

      {deficit.length > 0 && (
        <div className="notice sans">
          <IconShield />
          <span>
            {deficit.length} categoría{deficit.length === 1 ? '' : 's'} sin cobertura suficiente:{' '}
            {deficit.map((d) => d.catalog_name).join(', ')}.
          </span>
        </div>
      )}

      <div className="panel glass table-wrap">
        <table className="table sans">
          <thead>
            <tr>
              <th>Categoría</th>
              <th className="num">Ofrecido</th>
              <th className="num">Reservado</th>
              <th className="num">Entregado</th>
              <th className="num">Disponible</th>
              <th className="num">Demanda abierta</th>
              <th className="num">Balance</th>
            </tr>
          </thead>
          <tbody>
            {data.by_category.map((c) => (
              <tr key={c.catalog_code}>
                <td>{c.catalog_name}</td>
                <td className="num">{fmtQty(c.quantity, c.unit)}</td>
                <td className="num">{fmtQty(c.reserved_qty)}</td>
                <td className="num">{fmtQty(c.delivered_qty)}</td>
                <td className="num strong">{fmtQty(c.available_qty)}</td>
                <td className="num">{fmtQty(c.open_demand_qty)}</td>
                <td className="num">
                  <Pill tone={c.balance_qty < 0 ? 'danger' : 'ok'}>
                    {c.balance_qty > 0 ? '+' : ''}
                    {Number(c.balance_qty).toLocaleString('es-CO', {
                      maximumFractionDigits: 1,
                    })}
                  </Pill>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2 className="section-title">Ofertas registradas</h2>
      <p className="section-sub sans">
        La identidad del donante no es pública y no se expone en este listado.
      </p>
      <div className="panel glass table-wrap">
        <table className="table sans">
          <thead>
            <tr>
              <th>Tipo</th>
              <th>Recurso</th>
              <th>Estado</th>
              <th className="num">Cantidad</th>
              <th className="num">Disponible</th>
              <th>Condición</th>
              <th>Disponible desde</th>
            </tr>
          </thead>
          <tbody>
            {data.items.map((i) => (
              <tr key={i.item_id}>
                <td>
                  <Pill tone="info">{OFFER_TYPE_ES[i.offer_type] ?? i.offer_type}</Pill>
                </td>
                <td>{i.catalog_name ?? '—'}</td>
                <td>
                  <Pill tone={i.offer_status === 'AVAILABLE' ? 'ok' : 'neutral'}>
                    {OFFER_STATUS_ES[i.offer_status] ?? i.offer_status}
                  </Pill>
                </td>
                <td className="num">{fmtQty(i.quantity, i.unit)}</td>
                <td className="num strong">{fmtQty(i.available_qty)}</td>
                <td>{CONDITION_ES[i.condition] ?? '—'}</td>
                <td>{fmtDate(i.available_from)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

// ── Matching ───────────────────────────────────────────────────────────

function Matching() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  // El aviso vive aquí, no en la tarjeta: al aprobar o rechazar, el
  // candidato sale de la cola y su tarjeta se desmonta. Si el mensaje
  // viviera dentro, el operador no vería confirmación de lo que hizo.
  const [notice, setNotice] = useState(null)

  const load = useCallback(() => {
    fetchMatching()
      .then(setData)
      .catch((e) => setError(e.message))
  }, [])

  useEffect(() => {
    load()
  }, [load])

  if (error) return <p className="empty sans">{error}</p>
  if (!data) return <p className="empty sans">Cargando candidatos…</p>

  return (
    <>
      <div className="notice sans">
        <IconShield />
        <span>
          Los dos puntajes van separados a propósito: la prioridad humanitaria dice qué necesidad
          atender antes, la factibilidad dice qué tan fácil es cumplirla. Un recurso muy fácil no
          debe tapar una necesidad crítica difícil. La asignación siempre la aprueba una persona.
        </span>
      </div>

      {notice && (
        <p className={`action-note sans tone-${notice.tone} standalone`} role="status">
          {notice.text}
        </p>
      )}

      {data.items.length === 0 ? (
        <p className="empty sans">No hay candidatos pendientes de revisión.</p>
      ) : (
        <div className="match-grid">
          {data.items.map((m) => (
            <MatchCard
              key={m.match_id}
              m={m}
              onDone={(msg) => {
                setNotice(msg)
                load()
              }}
            />
          ))}
        </div>
      )}
    </>
  )
}

function MatchCard({ m, onDone }) {
  // La cantidad sugerida es lo que falta, topado por lo que hay libre en
  // la oferta: proponer más de lo disponible solo produciría un 409.
  const gap = Math.max((m.requested_qty ?? 0) - (m.covered_qty ?? 0), 0)
  const suggested = Math.min(gap, m.offer_available_qty ?? gap) || gap
  const [qty, setQty] = useState(String(suggested))
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)
  const [rejecting, setRejecting] = useState(false)
  const [reason, setReason] = useState('')

  async function approve() {
    setBusy(true)
    setResult(null)
    try {
      const alloc = await reserveAllocation(m.match_id, Number(qty))
      onDone({
        tone: 'ok',
        text:
          `${m.case_code} · ${m.catalog_name}: reservadas ${fmtQty(Number(qty), m.unit)}. ` +
          `La reserva vence ${fmtDate(alloc.expires_at)} y se libera sola si nadie la confirma.`,
      })
    } catch (e) {
      // El 409 de concurrencia trae la cantidad libre real: se muestra
      // tal cual para que el operador reintente con un número posible.
      setResult({ tone: 'danger', text: e.message })
    } finally {
      setBusy(false)
    }
  }

  async function reject() {
    setBusy(true)
    setResult(null)
    try {
      await rejectMatch(m.match_id, reason.trim())
      setRejecting(false)
      onDone({
        tone: 'muted',
        text: `${m.case_code} · ${m.catalog_name}: propuesta descartada. Motivo registrado: “${reason.trim()}”.`,
      })
    } catch (e) {
      setResult({ tone: 'danger', text: e.message })
    } finally {
      setBusy(false)
    }
  }

  return (
    <article className="match-card glass">
      <div className="match-head">
        <code>{m.case_code}</code>
        <Pill tone="warn">Por aprobar</Pill>
      </div>
      <h3 className="match-title">{m.catalog_name ?? 'Recurso'}</h3>

      <div className="score-row">
        <div className="score">
          <span className="score-label sans">Prioridad humanitaria</span>
          <div className="score-track">
            <div
              className="score-fill prio"
              style={{ width: `${Math.round(m.humanitarian_priority * 100)}%` }}
            />
          </div>
          <span className="score-value sans">{m.humanitarian_priority.toFixed(2)}</span>
        </div>
        <div className="score">
          <span className="score-label sans">Factibilidad</span>
          <div className="score-track">
            <div
              className="score-fill feas"
              style={{ width: `${Math.round(m.feasibility * 100)}%` }}
            />
          </div>
          <span className="score-value sans">{m.feasibility.toFixed(2)}</span>
        </div>
      </div>

      <dl className="facts sans compact">
        <div>
          <dt>Solicitado</dt>
          <dd>{fmtQty(m.requested_qty, m.unit)}</dd>
        </div>
        <div>
          <dt>Ya cubierto</dt>
          <dd>{fmtQty(m.covered_qty)}</dd>
        </div>
        <div>
          <dt>Disponible en la oferta</dt>
          <dd>{fmtQty(m.offer_available_qty)}</dd>
        </div>
        <div>
          <dt>Orden final</dt>
          <dd>{m.final_rank.toFixed(3)}</dd>
        </div>
      </dl>

      {m.explanation && (
        <details className="explain sans">
          <summary>Por qué se propone</summary>
          <pre>{JSON.stringify(m.explanation, null, 2)}</pre>
        </details>
      )}

      {result && (
        <p className={`action-note sans tone-${result.tone}`} role="status">
          {result.text}
        </p>
      )}

      {rejecting ? (
        <div className="reject-box">
          <label className="field sans" htmlFor={`r-${m.match_id}`}>
            Motivo del rechazo
            <input
              id={`r-${m.match_id}`}
              type="text"
              value={reason}
              placeholder="p. ej. la familia ya recibió este ítem"
              onChange={(e) => setReason(e.target.value)}
            />
          </label>
          <div className="match-actions">
            <button
              className="btn"
              type="button"
              disabled={busy || reason.trim().length < 3}
              onClick={reject}
            >
              Confirmar rechazo
            </button>
            <button className="btn secondary" type="button" onClick={() => setRejecting(false)}>
              Volver
            </button>
          </div>
        </div>
      ) : (
        <>
          <label className="field sans qty-field" htmlFor={`q-${m.match_id}`}>
            Cantidad a reservar
            <input
              id={`q-${m.match_id}`}
              type="number"
              min="0.1"
              step="0.1"
              max={m.offer_available_qty ?? undefined}
              value={qty}
              onChange={(e) => setQty(e.target.value)}
            />
          </label>
          <div className="match-actions">
            <button
              className="btn"
              type="button"
              disabled={busy || !(Number(qty) > 0)}
              onClick={approve}
            >
              {busy ? 'Reservando…' : 'Aprobar y reservar'}
            </button>
            <button
              className="btn secondary"
              type="button"
              disabled={busy}
              onClick={() => setRejecting(true)}
            >
              Rechazar
            </button>
          </div>
        </>
      )}
    </article>
  )
}

// ── Logística ──────────────────────────────────────────────────────────

function Logistics() {
  const [data, setData] = useState(null)
  useEffect(() => {
    fetchAllocations()
      .then(setData)
      .catch(() => setData({ items: [] }))
  }, [])

  if (!data) return <p className="empty sans">Cargando asignaciones…</p>

  return (
    <>
      <h2 className="section-title">Asignaciones</h2>
      <p className="section-sub sans">
        Cada reserva bloquea cantidad de una oferta y expira si no se confirma.
      </p>
      <div className="panel glass table-wrap">
        <table className="table sans">
          <thead>
            <tr>
              <th>Caso</th>
              <th>Recurso</th>
              <th>Estado</th>
              <th className="num">Asignado</th>
              <th className="num">Cumplido</th>
              <th>Expira</th>
            </tr>
          </thead>
          <tbody>
            {data.items.map((a) => (
              <tr key={a.allocation_id}>
                <td>
                  <code>{a.case_code ?? '—'}</code>
                </td>
                <td>{a.catalog_name ?? '—'}</td>
                <td>
                  <Pill tone={a.status === 'RESERVED' ? 'warn' : 'ok'}>
                    {ALLOCATION_STATUS_ES[a.status] ?? a.status}
                  </Pill>
                </td>
                <td className="num">{fmtQty(a.allocated_qty, a.unit)}</td>
                <td className="num">{fmtQty(a.fulfilled_qty)}</td>
                <td>{fmtDate(a.expires_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {data.items.length === 0 && <p className="empty sans">Sin asignaciones.</p>}
      </div>
    </>
  )
}

// ── Shell ──────────────────────────────────────────────────────────────

const NAV = [
  { key: 'overview', label: 'Tablero' },
  { key: 'cases', label: 'Casos' },
  { key: 'needs', label: 'Necesidades' },
  { key: 'resources', label: 'Recursos' },
  { key: 'matching', label: 'Matching' },
  { key: 'logistics', label: 'Logística' },
]

export default function App() {
  const [user, setUser] = useState(getToken() ? getStoredUser() : null)
  const [view, setView] = useState('overview')
  const [filter, setFilter] = useState('')
  const [openCaseId, setOpenCaseId] = useState(null)
  const [overview, setOverview] = useState(null)
  const [error, setError] = useState(null)

  const loadOverview = useCallback(() => {
    fetchOverview()
      .then(setOverview)
      .catch((e) => {
        if (e instanceof AuthError) setUser(null)
        else setError(e.message)
      })
  }, [])

  useEffect(() => {
    if (user) loadOverview()
  }, [user, loadOverview])

  if (!user) {
    return (
      <>
        <div className="backdrop" aria-hidden="true">
          <img src={`${IMG}/territorio-1600.webp`} alt="" width={1600} height={900} />
        </div>
        <div className="ambient" aria-hidden="true" />
        <Login onDone={setUser} />
      </>
    )
  }

  function go(nextView, nextFilter = '') {
    setFilter(nextFilter)
    setView(nextView)
  }

  return (
    <>
      {/* Dentro de la consola no va fotografía de fondo: son tablas densas y
          la foto le come contraste al dato. El degradado de campaña se queda. */}
      <div className="ambient" aria-hidden="true" />

      <header className="masthead">
        <div className="masthead-inner">
          <div>
            <h1 className="wordmark">
              Colombia <span>Unida</span>
            </h1>
            <p className="tagline sans">
              Consola de operación · Terremoto del 10 de agosto de 2026
            </p>
          </div>
          <div className="user-box sans">
            <span className="user-roles">{user.roles?.join(' · ')}</span>
            <span className="user-email">{user.email}</span>
            <button
              className="mini-btn sans"
              type="button"
              onClick={() => {
                logout()
                setUser(null)
              }}
            >
              Salir
            </button>
          </div>
        </div>
        <nav className="tabs" role="tablist" aria-label="Secciones de la consola">
          {NAV.map((item) => (
            <button
              key={item.key}
              className="tab sans"
              role="tab"
              aria-selected={view === item.key}
              onClick={() => go(item.key)}
              type="button"
            >
              {item.label}
            </button>
          ))}
        </nav>
      </header>

      <main>
        {error && <p className="empty sans">{error}</p>}

        {view === 'overview' &&
          (overview ? (
            <Overview data={overview} onGo={go} />
          ) : (
            <p className="empty sans">Cargando…</p>
          ))}
        {view === 'cases' && <Cases filter={filter} onOpenCase={setOpenCaseId} />}
        {view === 'needs' && <Needs filter={filter} />}
        {view === 'resources' && <Resources />}
        {view === 'matching' && <Matching />}
        {view === 'logistics' && <Logistics />}

        <p className="privacy-note sans">
          Consola de datos protegidos. Los campos sensibles —documento, teléfono, ubicación exacta,
          salud— no se muestran en estas vistas; consultarlos exige justificación y queda
          registrado. Quien modifica una validación o una asignación no puede aprobar su propio
          acto.
        </p>
      </main>

      {openCaseId && <CaseDetail caseId={openCaseId} onClose={() => setOpenCaseId(null)} />}
    </>
  )
}
