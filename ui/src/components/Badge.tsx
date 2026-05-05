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
    text: 'var(--bg0)',
  },
  transcoding: {
    background: 'var(--state-transcoding)',
    border: 'var(--state-transcoding)',
    text: 'var(--bg0)',
  },
  queued: {
    background: 'var(--state-queued)',
    border: 'var(--state-queued)',
    text: 'var(--bg0)',
  },
  failed: {
    background: 'var(--state-failed)',
    border: 'var(--state-failed)',
    text: 'var(--bg0)',
  },
  downloading: {
    background: 'var(--state-downloading)',
    border: 'var(--state-downloading)',
    text: 'var(--bg0)',
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
