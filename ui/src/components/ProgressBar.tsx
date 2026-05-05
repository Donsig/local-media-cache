type ProgressBarProps = {
  value: number
  label?: string
}

export function ProgressBar({ value, label }: ProgressBarProps) {
  const clamped = Math.max(0, Math.min(1, value))

  return (
    <div className="progress">
      <div className="progress__track" aria-hidden="true">
        <div className="progress__fill" style={{ width: `${clamped * 100}%` }} />
      </div>
      {label ? <div className="progress__label">{label}</div> : null}
    </div>
  )
}
