import type { ButtonHTMLAttributes, PropsWithChildren } from 'react'

type BtnProps = PropsWithChildren<
  ButtonHTMLAttributes<HTMLButtonElement> & {
    variant?: 'primary' | 'secondary' | 'danger'
    size?: 'default' | 'small'
  }
>

export function Btn({
  children,
  className = '',
  variant = 'secondary',
  size = 'default',
  type = 'button',
  ...props
}: BtnProps) {
  return (
    <button
      type={type}
      className={`btn btn--${variant} ${size === 'small' ? 'btn--small' : ''} ${className}`.trim()}
      {...props}
    >
      {children}
    </button>
  )
}
