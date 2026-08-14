import { useEffect, useMemo, useState } from 'react'
import { fetchFeed, fetchImpact } from './api.js'
import './styles.css'

const CATALOG_LABELS = {
  'SHELTER.MATTRESS': 'Colchones',
  'SHELTER.BLANKET': 'Cobijas',
  'SHELTER.TENT': 'Carpas',
  'SHELTER.KIT': 'Kit de alojamiento',
  'FOOD.RATION': 'Mercados',
  'FOOD.INFANT': 'Alimentación infantil',
  'WATER.BOTTLED': 'Agua potable',
  'WATER.TANK': 'Tanques de agua',
  'HYGIENE.KIT': 'Kits de aseo',
  'HYGIENE.DIAPERS': 'Pañales',
  'HOUSING.TARP': 'Plástico / lona',
  'HOUSING.ROOF.REPAIR': 'Reparación de techo',
  'HOUSING.MATERIALS': 'Materiales',
  'HEALTH.MEDICATION': 'Medicamentos',
  'HEALTH.FIRST_AID': 'Primeros auxilios',
  'CLOTHING.ADULT': 'Ropa adulto',
  'CLOTHING.CHILD': 'Ropa infantil',
  'SERVICES.DEBRIS': 'Remoción de escombros',
  'TRANSPORT.CARGO': 'Transporte de carga',
}

const label = (code) => CATALOG_LABELS[code] ?? code
const titleCase = (s) =>
  (s ?? '').toLowerCase().replace(/(^|\s|-)([a-záéíóúñ])/g, (_, p, c) => p + c.toUpperCase())

function Kpi({ label: text, value, note }) {
  return (
    <div className="kpi">
      <p className="kpi-label sans">{text}</p>
      <p className="kpi-value">{value}</p>
      {note && <p className="kpi-note sans">{note}</p>}
    </div>
  )
}

function Bars({ rows, accent = false }) {
  const max = Math.max(1, ...rows.map((r) => r.value))
  return (
    <div>
      {rows.map((r) => (
        <div className="bar-row" key={r.key}>
          <span className="bar-label sans">{r.label}</span>
          {r.suppressed ? (
            <span className="suppressed sans">
              dato suprimido — menos casos que el umbral de privacidad
            </span>
          ) : (
            <div className="bar-track">
              <div
                className={`bar-fill${accent ? ' accent' : ''}`}
                style={{ width: `${Math.round((r.value / max) * 100)}%` }}
              />
            </div>
          )}
          <span className="bar-value sans">{r.suppressed ? '—' : r.display ?? r.value}</span>
        </div>
      ))}
    </div>
  )
}

function Dashboard({ impact }) {
  const m = impact.metrics ?? {}
  const val = (k) => (m[k] ? m[k].value : 0)
  const received = val('cases_received')
  const verified = val('cases_verified')
  const served = val('cases_served')

  const horizons = [
    ['emergency', 'Emergencia (horas/días)'],
    ['recovery', 'Recuperación (semanas/meses)'],
    ['reconstruction', 'Reconstrucción (meses/años)'],
  ]
    .filter(([k]) => m[`needs_count_${k}`])
    .map(([k, name]) => ({
      key: k,
      label: name,
      value: val(`needs_count_${k}`),
      display: `${val(`needs_count_${k}`)} nec.`,
    }))

  const coverage = horizons.map(({ key, label: name }) => ({
    key: `cov-${key}`,
    label: name,
    value: val(`needs_coverage_pct_${key}`),
    display: `${val(`needs_coverage_pct_${key}`)}%`,
  }))

  const categories = (impact.top_categories ?? []).map((c) => ({
    key: c.catalog_code,
    label: label(c.catalog_code),
    value: c.value,
    suppressed: c.suppressed,
  }))

  const municipalities = (impact.by_municipality ?? [])
    .filter((x) => x.metric === 'cases_received')
    .map((x) => ({
      key: x.municipality,
      label: titleCase(x.municipality),
      value: x.value,
      suppressed: x.suppressed,
    }))

  const asOf = impact.as_of
    ? new Date(impact.as_of).toLocaleString('es-CO', {
        dateStyle: 'medium',
        timeStyle: 'short',
      })
    : '—'

  return (
    <>
      <div className="notice sans">
        Cifras derivadas de datos verificados y agregados. Las celdas con menos de{' '}
        {impact.privacy_threshold ?? 5} casos se suprimen para no permitir reidentificar a
        hogares en zonas poco pobladas.
      </div>

      <div className="kpi-grid">
        <Kpi label="Casos recibidos" value={received} note="Por WhatsApp y otros canales" />
        <Kpi
          label="Casos verificados"
          value={verified}
          note={received ? `${Math.round((verified / received) * 100)}% del total` : '—'}
        />
        <Kpi
          label="Casos con ayuda entregada"
          value={served}
          note="Con entrega confirmada por un validador"
        />
        <Kpi
          label="Corte de la información"
          value={asOf.split(',')[0]}
          note={`Actualizado ${asOf.split(', ')[1] ?? ''}`}
        />
      </div>

      {horizons.length > 0 && (
        <>
          <h2 className="section-title">Necesidades por horizonte</h2>
          <p className="section-sub sans">
            Cada necesidad se gestiona por separado, con su propia cantidad y estado.
          </p>
          <div className="panel">
            <Bars rows={horizons} />
          </div>

          <h2 className="section-title">Cobertura por horizonte</h2>
          <p className="section-sub sans">
            Cantidad ya cubierta sobre la cantidad confirmada por el equipo de validación.
          </p>
          <div className="panel">
            <Bars rows={coverage} accent />
          </div>
        </>
      )}

      {categories.length > 0 && (
        <>
          <h2 className="section-title">Qué se está necesitando más</h2>
          <p className="section-sub sans">Necesidades registradas por categoría del catálogo.</p>
          <div className="panel">
            <Bars rows={categories} />
          </div>
        </>
      )}

      {municipalities.length > 0 && (
        <>
          <h2 className="section-title">Casos por municipio</h2>
          <p className="section-sub sans">
            Solo se muestra municipio y departamento; nunca la ubicación exacta de un hogar.
          </p>
          <div className="panel">
            <Bars rows={municipalities} accent />
          </div>
        </>
      )}

      <h2 className="section-title">Cómo leer estas cifras</h2>
      <div className="panel">
        <dl className="defs sans">
          {Object.entries(impact.definitions ?? {}).map(([k, v]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
      </div>
    </>
  )
}

function CaseCard({ item }) {
  const pct = Math.round(item.progress_percent ?? 0)
  const place = [item.location?.admin2, item.location?.admin1]
    .filter(Boolean)
    .map(titleCase)
    .join(' · ')
  return (
    <article className="case-card">
      <p className="case-place sans">{place || 'Ubicación por confirmar'}</p>
      <h3 className="case-title">{item.title}</h3>
      <p className="case-summary">{item.summary}</p>

      {item.updates?.length > 0 && (
        <ul className="updates sans">
          {item.updates.slice(0, 2).map((u, i) => (
            <li className="update" key={i}>
              <span className="update-dot" />
              <span>
                <strong>{u.title}</strong>
                {u.body ? ` — ${u.body}` : ''}
              </span>
            </li>
          ))}
        </ul>
      )}

      <div>
        <div className="meta-row sans">
          <span>
            {item.household_size_band
              ? `Hogar de ${item.household_size_band} personas`
              : 'Tamaño de hogar por confirmar'}
          </span>
          <span>{pct}% cubierto</span>
        </div>
        <div className="progress-track" style={{ marginTop: '0.35rem' }}>
          <div
            className={`progress-fill${pct < 34 ? ' low' : ''}`}
            style={{ width: `${Math.max(pct, 2)}%` }}
          />
        </div>
      </div>

      <button className={`btn${pct >= 100 ? ' secondary' : ''}`} type="button">
        {pct >= 100 ? 'Ver el avance de este caso' : 'Ayudar a este caso'}
      </button>
    </article>
  )
}

function Feed({ items, order, setOrder, admin1, setAdmin1, departments }) {
  return (
    <>
      <div className="notice sans">
        Cada caso se publica solo con el consentimiento de la familia y tras revisión del
        equipo. No mostramos nombres, teléfonos, documentos ni la dirección exacta.
      </div>

      <div className="filters">
        <button
          className="chip sans"
          aria-pressed={order === 'recent'}
          onClick={() => setOrder('recent')}
          type="button"
        >
          Más recientes
        </button>
        <button
          className="chip sans"
          aria-pressed={order === 'gap'}
          onClick={() => setOrder('gap')}
          type="button"
        >
          Mayor brecha
        </button>
        <select
          className="chip sans"
          value={admin1}
          onChange={(e) => setAdmin1(e.target.value)}
          aria-label="Filtrar por departamento"
        >
          <option value="">Todos los departamentos</option>
          {departments.map((d) => (
            <option key={d} value={d}>
              {titleCase(d)}
            </option>
          ))}
        </select>
        <span className="sans" style={{ fontSize: '0.85rem', color: 'var(--ink-faint)' }}>
          {items.length} caso{items.length === 1 ? '' : 's'} publicado
          {items.length === 1 ? '' : 's'}
        </span>
      </div>

      {items.length === 0 ? (
        <p className="state sans">No hay casos publicados con estos filtros.</p>
      ) : (
        <div className="cards">
          {items.map((item) => (
            <CaseCard item={item} key={item.slug} />
          ))}
        </div>
      )}
    </>
  )
}

export default function App() {
  const [tab, setTab] = useState('feed')
  const [impact, setImpact] = useState(null)
  const [feed, setFeed] = useState(null)
  const [order, setOrder] = useState('recent')
  const [admin1, setAdmin1] = useState('')
  const [error, setError] = useState(null)

  useEffect(() => {
    fetchImpact().then(setImpact).catch((e) => setError(e.message))
  }, [])

  useEffect(() => {
    fetchFeed({ order, admin1: admin1 || undefined, limit: 50 })
      .then(setFeed)
      .catch((e) => setError(e.message))
  }, [order, admin1])

  const departments = useMemo(() => {
    const set = new Set()
    for (const m of impact?.by_municipality ?? []) set.add(m.municipality)
    for (const i of feed?.items ?? []) if (i.location?.admin1) set.add(i.location.admin1)
    return [...set].filter((d) => (feed?.items ?? []).some((i) => i.location?.admin1 === d)).sort()
  }, [impact, feed])

  return (
    <>
      <header className="masthead">
        <div className="masthead-inner">
          <div>
            <h1 className="wordmark">
              Colombia <span>Unida</span>
            </h1>
            <p className="tagline sans">
              Ayuda humanitaria coordinada · Terremoto del 10 de agosto de 2026
            </p>
          </div>
          <span className="demo-flag sans">Datos de demostración</span>
        </div>
        <nav className="tabs" role="tablist">
          <button
            className="tab sans"
            role="tab"
            aria-selected={tab === 'feed'}
            onClick={() => setTab('feed')}
            type="button"
          >
            Casos que necesitan ayuda
          </button>
          <button
            className="tab sans"
            role="tab"
            aria-selected={tab === 'impact'}
            onClick={() => setTab('impact')}
            type="button"
          >
            Transparencia
          </button>
        </nav>
      </header>

      <main>
        {error && (
          <p className="state sans">
            No pudimos cargar la información ({error}). Reintenta en un momento.
          </p>
        )}

        {!error && tab === 'feed' &&
          (feed ? (
            <Feed
              items={feed.items}
              order={order}
              setOrder={setOrder}
              admin1={admin1}
              setAdmin1={setAdmin1}
              departments={departments}
            />
          ) : (
            <p className="state sans">Cargando casos…</p>
          ))}

        {!error && tab === 'impact' &&
          (impact ? <Dashboard impact={impact} /> : <p className="state sans">Cargando cifras…</p>)}

        <p className="privacy-note sans">
          Colombia Unida publica únicamente una proyección segura y auditable de la operación.
          Los datos de identidad, contacto y ubicación exacta de las familias permanecen en el
          plano protegido y no se exponen en esta página. Cada ayuda entregada queda registrada
          con evidencia y responsable.
        </p>
      </main>
    </>
  )
}
