import type { SVGProps } from 'react'

type IconProps = SVGProps<SVGSVGElement>

function iconDefaults(props: IconProps) {
  return {
    width: 16,
    height: 16,
    viewBox: '0 0 16 16',
    fill: 'none',
    xmlns: 'http://www.w3.org/2000/svg',
    ...props,
  }
}

export function IcoTV(props: IconProps) {
  return (
    <svg {...iconDefaults(props)}>
      <rect x="1.75" y="2.25" width="12.5" height="9" rx="1.5" stroke="currentColor" strokeWidth="1.4" />
      <path d="M5 13.75h6M8 11.25v2.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  )
}

export function IcoFilm(props: IconProps) {
  return (
    <svg {...iconDefaults(props)}>
      <rect x="2" y="2.75" width="12" height="10.5" rx="1.5" stroke="currentColor" strokeWidth="1.4" />
      <path
        d="M5 2.75v10.5M11 2.75v10.5M2 6.25h3M2 9.75h3M11 6.25h3M11 9.75h3"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinecap="round"
      />
    </svg>
  )
}

export function IcoChevR(props: IconProps) {
  return (
    <svg {...iconDefaults(props)}>
      <path d="M6 4l4 4-4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

export function IcoSearch(props: IconProps) {
  return (
    <svg {...iconDefaults(props)}>
      <circle cx="7" cy="7" r="4.5" stroke="currentColor" strokeWidth="1.4" />
      <path d="M10.5 10.5L14 14" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  )
}

export function IcoPlus(props: IconProps) {
  return (
    <svg {...iconDefaults(props)}>
      <path d="M8 3v10M3 8h10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  )
}
