type PillTab = {
  label: string
  value: string
}

type PillTabsProps = {
  tabs: PillTab[]
  active: string
  onChange: (value: string) => void
}

export function PillTabs({ tabs, active, onChange }: PillTabsProps) {
  return (
    <div className="pill-tabs" role="tablist" aria-label="Filters">
      {tabs.map((tab) => (
        <button
          key={tab.value}
          type="button"
          className={`pill-tabs__button${tab.value === active ? ' pill-tabs__button--active' : ''}`}
          onClick={() => onChange(tab.value)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  )
}
