import { useMemo, useState } from 'react'
import { ClientsScreen } from './screens/ClientsScreen'
import { LibraryScreen } from './screens/LibraryScreen'
import { ProfilesScreen } from './screens/ProfilesScreen'
import { SettingsScreen } from './screens/SettingsScreen'

type Section = 'library' | 'clients' | 'profiles' | 'settings'

type NavItem = {
  label: string
  value: Section
  eyebrow: string
}

const navItems: NavItem[] = [
  { label: 'Library', value: 'library', eyebrow: 'Browse media' },
  { label: 'Clients', value: 'clients', eyebrow: 'Manage devices' },
  { label: 'Profiles', value: 'profiles', eyebrow: 'Transcode presets' },
  { label: 'Settings', value: 'settings', eyebrow: 'Server token' },
]

function App() {
  const [activeSection, setActiveSection] = useState<Section>('library')

  const screen = useMemo(() => {
    switch (activeSection) {
      case 'clients':
        return <ClientsScreen />
      case 'profiles':
        return <ProfilesScreen />
      case 'settings':
        return <SettingsScreen />
      case 'library':
      default:
        return <LibraryScreen />
    }
  }, [activeSection])

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-block">
          <div className="section-label">Syncarr</div>
          <h1 className="brand-title">Local Media Cache</h1>
          <p className="brand-copy">
            Stage 2.5 UI scaffold for browsing libraries and managing server-side resources.
          </p>
        </div>

        <nav className="nav-list" aria-label="Primary">
          {navItems.map((item) => {
            const active = item.value === activeSection

            return (
              <button
                key={item.value}
                type="button"
                className={`nav-item${active ? ' nav-item--active' : ''}`}
                onClick={() => setActiveSection(item.value)}
              >
                <span className="nav-item__eyebrow">{item.eyebrow}</span>
                <span className="nav-item__label">{item.label}</span>
              </button>
            )
          })}
        </nav>
      </aside>

      <main className="main-panel">{screen}</main>
    </div>
  )
}

export default App
