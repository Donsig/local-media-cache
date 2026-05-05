import type { ChangeEventHandler, ReactNode } from 'react'

type SynInputProps = {
  value: string
  onChange: ChangeEventHandler<HTMLInputElement>
  placeholder: string
  prefixIcon?: ReactNode
}

export function SynInput({ value, onChange, placeholder, prefixIcon }: SynInputProps) {
  return (
    <label className="syn-input">
      {prefixIcon ? <span className="syn-input__icon">{prefixIcon}</span> : null}
      <input className="syn-input__control" value={value} onChange={onChange} placeholder={placeholder} />
    </label>
  )
}
