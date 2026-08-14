import { useEffect, useMemo, useRef, useState } from 'react'
import { fetchCase, fetchFeed, fetchHelpOptions, fetchImpact, submitHelpOffer } from './api.js'
import {
  IconArrow,
  IconCheck,
  IconClock,
  IconDelivered,
  IconDot,
  IconEyeOff,
  IconHeart,
  IconInbox,
  IconShield,
  IconVerified,
} from './icons.jsx'
import './styles.css'

const IMG = `${import.meta.env.BASE_URL}img`

// Fotografía de contexto: ambiente, no identificación de personas reales.
const PHOTOS = ['cosecha', 'comunidad', 'manos', 'territorio']

const CATALOG_LABELS = {
  'SHELTER.MATTRESS': 'Colchones',
  'SHELTER.BLANKET': 'Cobijas',
  'SHELTER.TENT': 'Carpas',
  'SHELTER.KIT': 'Kit de alojamiento',
  'FOOD.RATION': 'Mercados',
  'FOOD.INFANT': 'Alimentación infantil',
  'FOOD.HOT_MEAL': 'Comida caliente',
  'WATER.BOTTLED': 'Agua potable',
  'WATER.TANK': 'Tanques de agua',
  'HYGIENE.KIT': 'Kits de aseo',
  'HYGIENE.DIAPERS': 'Pañales',
  'HYGIENE.FEMININE': 'Higiene menstrual',
  'HOUSING.TARP': 'Plástico / lona',
  'HOUSING.ROOF.REPAIR': 'Reparación de techo',
  'HOUSING.WALL.REPAIR': 'Reparación de muros',
  'HOUSING.MATERIALS': 'Materiales',
  'HEALTH.MEDICATION': 'Medicamentos',
  'HEALTH.FIRST_AID': 'Primeros auxilios',
  'CLOTHING.ADULT': 'Ropa adulto',
  'CLOTHING.CHILD': 'Ropa infantil',
  'EDUCATION.KIT': 'Kits escolares',
  'SERVICES.DEBRIS': 'Remoción de escombros',
  'SERVICES.ENGINEERING': 'Evaluación estructural',
  'TRANSPORT.CARGO': 'Transporte de carga',
  'PSYCHOSOCIAL.SUPPORT': 'Apoyo psicosocial',
}

// unit_code viene en clave técnica; en la página va en español y en plural,
// que es como se lee al lado de una cantidad ("5 unidades").
const UNIT_ES = {
  UNIT: 'unidades',
  KIT: 'kits',
  SERVICE: 'servicios',
  TRIP: 'viajes',
  PERSON_DAY: 'días-persona',
  L: 'litros',
  M2: 'm²',
}

const label = (code) => CATALOG_LABELS[code] ?? code
const unitLabel = (code) => UNIT_ES[code] ?? (code ?? '').toLowerCase()
const titleCase = (s) =>
  (s ?? '').toLowerCase().replace(/(^|\s|-)([a-záéíóúñ])/g, (_, p, c) => p + c.toUpperCase())

/** Imagen responsiva con dimensiones reservadas (CLS < 0.1, regla #3).
 *  variant 'card' usa un recorte dedicado (encuadre y luz ya resueltos en
 *  el archivo, no con object-position sobre una foto panorámica). */
function Photo({ name, alt, width, height, priority = false, variant = 'wide', className }) {
  const card = variant === 'card'
  return (
    <img
      className={className}
      src={card ? `${IMG}/${name}-card.webp` : `${IMG}/${name}-1600.webp`}
      srcSet={card ? undefined : `${IMG}/${name}-800.webp 800w, ${IMG}/${name}-1600.webp 1600w`}
      sizes={card ? undefined : '(max-width: 760px) 100vw, 1180px'}
      width={width}
      height={height}
      alt={alt}
      loading={priority ? 'eager' : 'lazy'}
      decoding="async"
      // En minúscula: React 18 no reconoce fetchPriority en camelCase y lo
      // avisa por consola en cada render.
      fetchpriority={priority ? 'high' : 'auto'}
    />
  )
}

function Kpi({ icon: Icon, tint, label: text, value, note, isDate = false }) {
  return (
    <div className="kpi glass">
      <div className="kpi-head">
        <span className="kpi-icon" style={{ color: tint }}>
          <Icon />
        </span>
        <p className="kpi-label sans">{text}</p>
      </div>
      <p className={`kpi-value${isDate ? ' is-date' : ''}`}>{value}</p>
      {note && <p className="kpi-note sans">{note}</p>}
    </div>
  )
}

function Bars({ rows, accent = false }) {
  const max = Math.max(1, ...rows.filter((r) => !r.suppressed).map((r) => r.value))
  return (
    <div>
      {rows.map((r) => (
        <div className="bar-row" key={r.key}>
          <span className="bar-label sans">{r.label}</span>
          {r.suppressed ? (
            <span className="suppressed sans">
              <IconEyeOff />
              dato suprimido — menos casos que el umbral de privacidad
            </span>
          ) : (
            <div
              className="bar-track"
              role="img"
              aria-label={`${r.label}: ${r.display ?? r.value}`}
            >
              <div
                className={`bar-fill${accent ? ' accent' : ''}`}
                style={{ width: `${Math.max(3, Math.round((r.value / max) * 100))}%` }}
              />
            </div>
          )}
          <span className="bar-value sans">{r.suppressed ? '—' : (r.display ?? r.value)}</span>
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

  const horizonNames = {
    emergency: 'Emergencia (horas/días)',
    recovery: 'Recuperación (semanas/meses)',
    reconstruction: 'Reconstrucción (meses/años)',
  }
  const horizons = Object.entries(horizonNames)
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

  const asOf = impact.as_of ? new Date(impact.as_of) : null
  const asOfDate = asOf
    ? asOf.toLocaleDateString('es-CO', { day: '2-digit', month: 'short', year: 'numeric' })
    : '—'
  const asOfTime = asOf
    ? asOf.toLocaleTimeString('es-CO', { hour: '2-digit', minute: '2-digit' })
    : ''

  return (
    <>
      <div className="notice sans">
        <IconShield />
        <span>
          Cifras derivadas de datos verificados y agregados. Las celdas con menos de{' '}
          {impact.privacy_threshold ?? 5} casos se suprimen para que no sea posible reidentificar
          hogares en zonas poco pobladas.
        </span>
      </div>

      <div className="kpi-grid">
        <Kpi
          icon={IconInbox}
          tint="var(--sky)"
          label="Casos recibidos"
          value={received}
          note="Registrados por WhatsApp y otros canales"
        />
        <Kpi
          icon={IconVerified}
          tint="var(--emerald)"
          label="Casos verificados"
          value={verified}
          note={received ? `${Math.round((verified / received) * 100)}% del total recibido` : '—'}
        />
        <Kpi
          icon={IconDelivered}
          tint="var(--amber)"
          label="Con ayuda entregada"
          value={served}
          note="Entrega confirmada por un validador"
        />
        <Kpi
          icon={IconClock}
          tint="var(--ink-soft)"
          label="Corte de la información"
          isDate
          value={asOfDate}
          note={asOfTime ? `Actualizado a las ${asOfTime}` : ''}
        />
      </div>

      {horizons.length > 0 && (
        <>
          <h2 className="section-title">Necesidades por horizonte</h2>
          <p className="section-sub sans">
            Cada necesidad se gestiona por separado, con su propia cantidad y estado: la emergencia
            se mide en horas, la reconstrucción en meses.
          </p>
          <div className="panel glass">
            <Bars rows={horizons} />
          </div>

          <h2 className="section-title">Cobertura por horizonte</h2>
          <p className="section-sub sans">
            Cantidad ya cubierta sobre la cantidad confirmada por el equipo de validación.
          </p>
          <div className="panel glass">
            <Bars rows={coverage} accent />
          </div>
        </>
      )}

      {categories.length > 0 && (
        <>
          <h2 className="section-title">Qué se está necesitando más</h2>
          <p className="section-sub sans">
            Necesidades registradas por categoría del catálogo humanitario.
          </p>
          <div className="panel glass">
            <Bars rows={categories} />
          </div>
        </>
      )}

      {municipalities.length > 0 && (
        <>
          <h2 className="section-title">Casos por municipio</h2>
          <p className="section-sub sans">
            Solo municipio y departamento; nunca la ubicación exacta de un hogar.
          </p>
          <div className="panel glass">
            <Bars rows={municipalities} accent />
          </div>
        </>
      )}

      <h2 className="section-title">Cómo leer estas cifras</h2>
      <div className="panel glass">
        <dl className="defs">
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

/** Diálogo modal accesible.
 *
 *  Usa <dialog> nativo por showModal(): trae foco atrapado, cierre con Esc y
 *  el resto de la página inerte sin que haya que reimplementarlo a mano.
 */
function Modal({ open, onClose, labelledBy, children }) {
  const ref = useRef(null)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    if (open && !el.open) el.showModal()
    if (!open && el.open) el.close()
  }, [open])

  if (!open) return null
  return (
    <dialog
      className="modal glass"
      ref={ref}
      aria-labelledby={labelledBy}
      // Esc dispara 'cancel' y el click en el backdrop dispara 'close':
      // los dos tienen que avisarle al padre, o el estado queda desfasado
      // y el diálogo no vuelve a abrir.
      onCancel={(e) => {
        e.preventDefault()
        onClose()
      }}
      onClose={onClose}
      onClick={(e) => {
        if (e.target === ref.current) onClose()
      }}
    >
      <button className="modal-close" type="button" onClick={onClose} aria-label="Cerrar">
        ✕
      </button>
      {children}
    </dialog>
  )
}

/** «Ver el avance»: la historia pública completa del caso. */
function ProgressDialog({ slug, open, onClose }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!open) return
    setData(null)
    setError(null)
    fetchCase(slug)
      .then(setData)
      .catch(() => setError('No pudimos cargar el avance de este caso.'))
  }, [open, slug])

  const pct = Math.round(data?.progress_percent ?? 0)

  return (
    <Modal open={open} onClose={onClose} labelledBy="avance-titulo">
      <p className="modal-eyebrow sans">Avance del caso</p>
      <h2 className="modal-title" id="avance-titulo">
        {data?.title ?? 'Cargando…'}
      </h2>

      {error && <p className="form-error sans">{error}</p>}

      {data && (
        <>
          <p className="modal-lede sans">{data.summary}</p>

          <div className="modal-facts sans">
            <div>
              <dt>Ubicación</dt>
              <dd>
                {[data.location?.admin2, data.location?.admin1]
                  .filter(Boolean)
                  .map(titleCase)
                  .join(' · ') || 'Por confirmar'}
              </dd>
            </div>
            <div>
              <dt>Hogar</dt>
              <dd>
                {data.household_size_band
                  ? `${data.household_size_band} personas`
                  : 'Por confirmar'}
              </dd>
            </div>
            <div>
              <dt>Cobertura</dt>
              <dd>{pct}%</dd>
            </div>
          </div>

          <div
            className="progress-track"
            role="img"
            aria-label={`Cobertura de la necesidad: ${pct} por ciento`}
          >
            <div
              className={`progress-fill${pct < 34 ? ' low' : ''}`}
              style={{ width: `${Math.max(pct, 3)}%` }}
            />
          </div>

          <h3 className="modal-h3 sans">Lo que ha pasado</h3>
          {/* La API entrega lo más reciente primero (orden de feed); aquí se
              lee como relato, así que va en orden cronológico. */}
          {data.updates?.length ? (
            <ol className="timeline-pub sans">
              {[...data.updates].reverse().map((u, i) => (
                <li key={i}>
                  {u.type === 'DELIVERY' ? <IconCheck /> : <IconDot />}
                  <div>
                    <strong>{u.title}</strong>
                    {u.body && <p>{u.body}</p>}
                    {u.occurred_on && <span className="muted">{u.occurred_on}</span>}
                  </div>
                </li>
              ))}
            </ol>
          ) : (
            <p className="modal-lede sans">Todavía no hay actualizaciones publicadas.</p>
          )}

          <p className="modal-note sans">
            <IconShield />
            <span>
              Este avance solo muestra lo que la familia autorizó publicar. No incluye nombres,
              teléfonos, documentos ni la dirección exacta.
            </span>
          </p>
        </>
      )}
    </Modal>
  )
}

const OFFER_TYPES = [
  { value: 'IN_KIND', label: 'Cosas que puedo entregar' },
  { value: 'MONEY', label: 'Dinero' },
  { value: 'TRANSPORT', label: 'Transporte' },
  { value: 'VOLUNTEERING', label: 'Mi tiempo como voluntario' },
  { value: 'SERVICE', label: 'Un servicio profesional' },
]

/** «Ayudar a este caso»: formulario de ofrecimiento. */
function HelpDialog({ slug, caseTitle, open, onClose }) {
  const [options, setOptions] = useState([])
  const [form, setForm] = useState({
    offer_type: 'IN_KIND',
    catalog_code: '',
    quantity: '',
    amount_cop: '',
    contact_name: '',
    contact_phone: '',
    contact_email: '',
    message: '',
    consent_contact: false,
  })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [done, setDone] = useState(null)

  const set = (k) => (e) =>
    setForm((f) => ({
      ...f,
      [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value,
    }))

  useEffect(() => {
    if (!open || !slug) return
    setDone(null)
    setError(null)
    fetchHelpOptions(slug)
      .then((r) => {
        setOptions(r.options ?? [])
        // Preselecciona lo que más falta: es la ayuda más útil y le ahorra
        // una decisión a quien quiere ayudar rápido.
        const top = [...(r.options ?? [])].sort((a, b) => b.pending_qty - a.pending_qty)[0]
        if (top) {
          setForm((f) => ({
            ...f,
            catalog_code: top.catalog_code,
            quantity: String(top.pending_qty),
          }))
        }
      })
      .catch(() => setOptions([]))
  }, [open, slug])

  const isMoney = form.offer_type === 'MONEY'
  const selected = options.find((o) => o.catalog_code === form.catalog_code)

  async function submit(e) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const payload = {
        slug: slug ?? null,
        offer_type: form.offer_type,
        contact_name: form.contact_name.trim(),
        contact_phone: form.contact_phone.trim(),
        contact_email: form.contact_email.trim() || null,
        message: form.message.trim() || null,
        consent_contact: form.consent_contact,
      }
      if (isMoney) {
        payload.amount_cop = Number(form.amount_cop)
      } else {
        payload.catalog_code = form.catalog_code || null
        payload.quantity = Number(form.quantity)
      }
      setDone(await submitHelpOffer(payload))
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal open={open} onClose={onClose} labelledBy="ayudar-titulo">
      <p className="modal-eyebrow sans">Ofrecer ayuda</p>
      <h2 className="modal-title" id="ayudar-titulo">
        {done ? 'Gracias' : (caseTitle ?? 'Ayudar')}
      </h2>

      {done ? (
        <>
          <p className="modal-lede sans">{done.next_step}</p>
          <div className="modal-facts sans">
            <div>
              <dt>Tu referencia</dt>
              <dd>
                <code>{done.reference}</code>
              </dd>
            </div>
          </div>
          <p className="modal-note sans">
            <IconShield />
            <span>
              Tu ofrecimiento no queda publicado y tus datos de contacto no son públicos: se guardan
              cifrados y solo los ve el equipo que coordina la entrega.
            </span>
          </p>
          <button className="btn" type="button" onClick={onClose}>
            Listo
          </button>
        </>
      ) : (
        <form className="help-form" onSubmit={submit}>
          {options.length > 0 && (
            <p className="modal-lede sans">
              Lo que falta en este caso:{' '}
              {options
                .map((o) => `${o.name.toLowerCase()} (${o.pending_qty} ${unitLabel(o.unit)})`)
                .join(', ')}
              .
            </p>
          )}

          <label className="field sans">
            <span>¿Cómo quieres ayudar?</span>
            <select value={form.offer_type} onChange={set('offer_type')}>
              {OFFER_TYPES.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>

          {isMoney ? (
            <label className="field sans">
              <span>Monto que puedes aportar (COP)</span>
              <input
                type="number"
                min="1000"
                step="1000"
                required
                value={form.amount_cop}
                onChange={set('amount_cop')}
                placeholder="200000"
              />
              <small>
                La plataforma no recibe ni administra dinero: queda registrado como promesa y el
                equipo te explica cómo hacerlo llegar.
              </small>
            </label>
          ) : (
            <>
              {options.length > 0 && (
                <label className="field sans">
                  <span>¿Qué puedes aportar?</span>
                  <select value={form.catalog_code} onChange={set('catalog_code')}>
                    <option value="">Otra cosa (la describo abajo)</option>
                    {options.map((o) => (
                      <option key={o.catalog_code} value={o.catalog_code}>
                        {o.name} — faltan {o.pending_qty} {unitLabel(o.unit)}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <label className="field sans">
                <span>Cantidad{selected ? ` en ${unitLabel(selected.unit)}` : ''}</span>
                <input
                  type="number"
                  min="0.1"
                  step="0.1"
                  required
                  value={form.quantity}
                  onChange={set('quantity')}
                />
              </label>
            </>
          )}

          <label className="field sans">
            <span>Tu nombre o el de tu organización</span>
            <input
              type="text"
              required
              minLength={2}
              value={form.contact_name}
              onChange={set('contact_name')}
            />
          </label>

          <label className="field sans">
            <span>Teléfono de contacto</span>
            <input
              type="tel"
              required
              value={form.contact_phone}
              onChange={set('contact_phone')}
              placeholder="310 555 1234"
            />
          </label>

          <label className="field sans">
            <span>Correo (opcional)</span>
            <input type="email" value={form.contact_email} onChange={set('contact_email')} />
          </label>

          <label className="field sans">
            <span>¿Algo que debamos saber? (opcional)</span>
            <textarea
              rows={2}
              maxLength={1000}
              value={form.message}
              onChange={set('message')}
              placeholder="Cuándo y dónde podrías entregar"
            />
          </label>

          <label className="check sans">
            <input
              type="checkbox"
              checked={form.consent_contact}
              onChange={set('consent_contact')}
            />
            <span>
              Autorizo que el equipo de Colombia Unida me contacte por este teléfono para coordinar
              la ayuda.
            </span>
          </label>

          {error && (
            <p className="form-error sans" role="alert">
              {error}
            </p>
          )}

          <button className="btn" type="submit" disabled={busy || !form.consent_contact}>
            {busy ? 'Enviando…' : 'Enviar ofrecimiento'}
          </button>
          <p className="modal-note sans">
            <IconShield />
            <span>
              Nadie te va a pedir datos bancarios ni pagos en esta página. Un ofrecimiento no se
              publica y lo revisa una persona del equipo antes de coordinarlo.
            </span>
          </p>
        </form>
      )}
    </Modal>
  )
}

function CaseCard({ item, photo }) {
  const [dialog, setDialog] = useState(null)
  const pct = Math.round(item.progress_percent ?? 0)
  const complete = pct >= 100
  const place = [item.location?.admin2, item.location?.admin1]
    .filter(Boolean)
    .map(titleCase)
    .join(' · ')

  return (
    <article className="case-card glass">
      <div className="case-photo">
        <Photo
          name={photo}
          variant="card"
          width={720}
          height={300}
          alt="Comunidad colombiana en labores cotidianas; imagen de contexto, no del caso."
        />
        <p className="case-place sans">{place || 'Ubicación por confirmar'}</p>
      </div>

      <div className="case-body">
        <h3 className="case-title">{item.title}</h3>
        <p className="case-summary">{item.summary}</p>

        {item.updates?.length > 0 && (
          <ul className="updates sans">
            {item.updates.slice(0, 2).map((u, i) => (
              <li className={`update${u.type === 'NEED' ? ' pending' : ''}`} key={i}>
                {u.type === 'DELIVERY' ? <IconCheck /> : <IconDot />}
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
          <div
            className="progress-track"
            role="img"
            aria-label={`Cobertura de la necesidad: ${pct} por ciento`}
          >
            <div
              className={`progress-fill${pct < 34 ? ' low' : ''}`}
              style={{ width: `${Math.max(pct, 3)}%` }}
            />
          </div>
        </div>

        {/* Un caso cubierto al 100% ya no pide ayuda, muestra qué pasó; el
            resto abre el formulario. En los dos casos el botón principal
            abre un diálogo y el secundario deja la otra puerta disponible. */}
        <div className="case-actions">
          <button
            className={`btn${complete ? ' secondary' : ''}`}
            type="button"
            onClick={() => setDialog(complete ? 'progress' : 'help')}
          >
            {complete ? (
              <>
                Ver el avance <IconArrow />
              </>
            ) : (
              <>
                <IconHeart /> Ayudar a este caso
              </>
            )}
          </button>
          <button
            className="link-btn sans"
            type="button"
            onClick={() => setDialog(complete ? 'help' : 'progress')}
          >
            {complete ? 'Ayudar de todas formas' : 'Ver el avance'}
          </button>
        </div>
      </div>

      <ProgressDialog
        slug={item.slug}
        open={dialog === 'progress'}
        onClose={() => setDialog(null)}
      />
      <HelpDialog
        slug={item.slug}
        caseTitle={item.title}
        open={dialog === 'help'}
        onClose={() => setDialog(null)}
      />
    </article>
  )
}

function Feed({ items, order, setOrder, admin1, setAdmin1, departments }) {
  return (
    <>
      <div className="notice sans">
        <IconShield />
        <span>
          Cada caso se publica solo con el consentimiento de la familia y tras revisión del equipo.
          No mostramos nombres, teléfonos, documentos ni la dirección exacta. Las fotografías son de
          contexto y no corresponden a los hogares publicados.
        </span>
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
        <span className="filter-count sans">
          {items.length} caso{items.length === 1 ? '' : 's'} publicado
          {items.length === 1 ? '' : 's'}
        </span>
      </div>

      {items.length === 0 ? (
        <p className="state sans">No hay casos publicados con estos filtros.</p>
      ) : (
        <div className="cards">
          {items.map((item, i) => (
            <CaseCard item={item} photo={PHOTOS[i % PHOTOS.length]} key={item.slug} />
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
    fetchImpact()
      .then(setImpact)
      .catch((e) => setError(e.message))
  }, [])

  useEffect(() => {
    fetchFeed({ order, admin1: admin1 || undefined, limit: 50 })
      .then(setFeed)
      .catch((e) => setError(e.message))
  }, [order, admin1])

  const departments = useMemo(() => {
    const present = new Set((feed?.items ?? []).map((i) => i.location?.admin1).filter(Boolean))
    return [...present].sort()
  }, [feed])

  const m = impact?.metrics ?? {}
  const heroPhoto = tab === 'feed' ? 'cosecha' : 'comunidad'

  return (
    <>
      <div className="backdrop" aria-hidden="true">
        <Photo name="territorio" width={1600} height={900} alt="" priority />
      </div>
      <div className="ambient" aria-hidden="true" />

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
        <nav className="tabs" role="tablist" aria-label="Secciones">
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

      <section className="hero">
        <div className="hero-frame">
          <Photo
            name={heroPhoto}
            width={1180}
            height={340}
            priority
            alt="Comunidades colombianas trabajando juntas tras el terremoto."
          />
          <div className="hero-body">
            <p className="hero-eyebrow sans">
              {tab === 'feed' ? 'Necesidad → entrega → evidencia' : 'Transparencia auditable'}
            </p>
            <h2 className="hero-title">
              {tab === 'feed'
                ? 'Cada ayuda se sigue hasta la puerta de una familia'
                : 'Todo lo que se recibe y se entrega, a la vista'}
            </h2>
            <p className="hero-lede">
              {tab === 'feed'
                ? 'Los casos se registran por WhatsApp, un equipo los verifica en territorio y cada entrega queda con responsable y evidencia. Aquí solo se publica lo que la familia autorizó.'
                : 'Cifras agregadas y reproducibles: cuántos casos entran, cuántos se verifican y cuánta ayuda llega realmente, con la fecha de corte de cada número.'}
            </p>
            {impact && (
              <div className="hero-stats sans">
                <div className="hero-stat">
                  <strong>{m.cases_received?.value ?? 0}</strong>
                  <span>Casos recibidos</span>
                </div>
                <div className="hero-stat">
                  <strong>{m.cases_verified?.value ?? 0}</strong>
                  <span>Verificados</span>
                </div>
                <div className="hero-stat">
                  <strong>{feed?.items?.length ?? 0}</strong>
                  <span>Publicados con consentimiento</span>
                </div>
              </div>
            )}
          </div>
        </div>
      </section>

      <main>
        {error && (
          <p className="state sans">
            No pudimos cargar la información ({error}). Reintenta en un momento.
          </p>
        )}

        {!error &&
          tab === 'feed' &&
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

        {!error &&
          tab === 'impact' &&
          (impact ? <Dashboard impact={impact} /> : <p className="state sans">Cargando cifras…</p>)}

        <p className="privacy-note sans">
          Colombia Unida publica únicamente una proyección segura y auditable de la operación. La
          identidad, el contacto y la ubicación exacta de las familias permanecen en el plano
          protegido y no se exponen en esta página. Cada ayuda entregada queda registrada con
          responsable, hora y evidencia.
        </p>
      </main>
    </>
  )
}
