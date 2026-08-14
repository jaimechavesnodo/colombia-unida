// Iconos SVG en línea (regla #4: SVG, nunca emoji como icono).
// currentColor + aria-hidden: decorativos, el significado va en el texto.

const base = {
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.8,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  'aria-hidden': true,
  focusable: false,
}

export const IconInbox = (p) => (
  <svg {...base} {...p}>
    <path d="M3 13h4l2 3h6l2-3h4" />
    <path d="M5 5h14l2 8v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4z" />
  </svg>
)

export const IconVerified = (p) => (
  <svg {...base} {...p}>
    <path d="M9 12.5l2 2 4-4.5" />
    <path d="M12 3l7 3v6c0 4.2-2.9 7.7-7 9-4.1-1.3-7-4.8-7-9V6z" />
  </svg>
)

export const IconDelivered = (p) => (
  <svg {...base} {...p}>
    <path d="M3 7h10v9H3z" />
    <path d="M13 10h4l3 3v3h-7z" />
    <circle cx="7" cy="18" r="1.6" />
    <circle cx="17" cy="18" r="1.6" />
  </svg>
)

export const IconClock = (p) => (
  <svg {...base} {...p}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M12 7.5V12l3 2" />
  </svg>
)

export const IconShield = (p) => (
  <svg {...base} {...p}>
    <path d="M12 3l7 3v6c0 4.2-2.9 7.7-7 9-4.1-1.3-7-4.8-7-9V6z" />
    <path d="M12 10v4" />
    <circle cx="12" cy="8" r="0.6" fill="currentColor" stroke="none" />
  </svg>
)

export const IconEyeOff = (p) => (
  <svg {...base} {...p}>
    <path d="M3 3l18 18" />
    <path d="M10.6 6.2A8.6 8.6 0 0 1 12 6c5 0 9 6 9 6a15 15 0 0 1-2.3 2.9" />
    <path d="M6.2 8.1C4.3 9.6 3 12 3 12s4 6 9 6c1 0 2-.2 2.9-.5" />
    <path d="M9.9 9.9a3 3 0 0 0 4.2 4.2" />
  </svg>
)

export const IconCheck = (p) => (
  <svg {...base} {...p}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M8.5 12.3l2.3 2.2 4.7-5" />
  </svg>
)

export const IconDot = (p) => (
  <svg {...base} {...p}>
    <circle cx="12" cy="12" r="8.5" />
    <circle cx="12" cy="12" r="2.6" fill="currentColor" stroke="none" />
  </svg>
)

export const IconHeart = (p) => (
  <svg {...base} {...p}>
    <path d="M12 20s-7-4.3-7-9.2A4 4 0 0 1 12 8a4 4 0 0 1 7 2.8C19 15.7 12 20 12 20z" />
  </svg>
)

export const IconArrow = (p) => (
  <svg {...base} {...p}>
    <path d="M5 12h13" />
    <path d="M13 6l6 6-6 6" />
  </svg>
)
