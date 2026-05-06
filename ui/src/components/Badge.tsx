type BadgeColor = 'ready' | 'transcoding' | 'queued' | 'failed' | 'downloading' | 'default'

type BadgeProps = {
  color?: BadgeColor
  dot?: boolean
  label: string
}

const colorMap: Record<BadgeColor, { background: string; border: string; text: string }> = {
  ready: {
    background: 'var(--state-ready)',
    border: 'var(--state-ready)',
    text: 'var(--state-ready)',
  },
  transcoding: {
    background: 'var(--state-transcoding)',
    border: 'var(--state-transcoding)',
    text: 'var(--state-transcoding)',
  },
  queued: {
    background: 'var(--state-queued)',
    border: 'var(--state-queued)',
    text: 'var(--state-queued)',
  },
  failed: {
    background: 'var(--state-failed)',
    border: 'var(--state-failed)',
    text: 'var(--state-failed)',
  },
  downloading: {
    background: 'var(--state-downloading)',
    border: 'var(--state-downloading)',
    text: 'var(--state-downloading)',
  },
  default: {
    background: 'var(--bg4)',
    border: 'var(--border)',
    text: 'var(--text2)',
  },
}

export function Badge({ color = 'default', dot = false, label }: BadgeProps) {
  const palette = colorMap[color]

  return (
    <span
      className="badge"
      style={{
        background: color === 'default' ? palette.background : `${palette.background}22`,
        borderColor: palette.border,
        color: palette.text,
      }}
    >
      {dot ? <span className="badge__dot" /> : null}
      {label}
    </span>
  )
}
