import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { getTransferMode, setTransferMode } from './api'
import { Btn } from './components/Btn'
import { ClientsScreen } from './screens/ClientsScreen'
import { LibraryScreen } from './screens/LibraryScreen'
import { ProfilesScreen } from './screens/ProfilesScreen'
import { QueueScreen } from './screens/QueueScreen'
import { SettingsScreen } from './screens/SettingsScreen'
import type { TransferMode } from './types'

type Section = 'library' | 'queue' | 'clients' | 'profiles' | 'settings'

type NavItem = {
  label: string
  value: Section
  eyebrow: string
}

const navItems: NavItem[] = [
  { label: 'Library', value: 'library', eyebrow: 'Browse media' },
  { label: 'Queue', value: 'queue', eyebrow: 'Transfer status' },
  { label: 'Clients', value: 'clients', eyebrow: 'Manage devices' },
  { label: 'Profiles', value: 'profiles', eyebrow: 'Transcode presets' },
  { label: 'Settings', value: 'settings', eyebrow: 'Server token' },
]

function App() {
  const [activeSection, setActiveSection] = useState<Section>('library')
  const queryClient = useQueryClient()
  const modeQuery = useQuery({
    queryKey: ['transfer-mode'],
    queryFn: getTransferMode,
    refetchInterval: 15000,
  })
  const modeMutation = useMutation({
    mutationFn: setTransferMode,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['transfer-mode'] }),
  })
  const mode: TransferMode = modeQuery.data?.transfer_mode ?? 'running'

  const screen = useMemo(() => {
    switch (activeSection) {
      case 'queue':
        return <QueueScreen />
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
            Selective media sync to satellite servers.
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

        <div
          className="sidebar-controls"
          style={{ marginTop: 'auto', display: 'grid', gap: 8 }}
        >
          <Btn
            variant={mode === 'paused' ? 'primary' : 'secondary'}
            size="small"
            disabled={modeMutation.isPending}
            onClick={() => modeMutation.mutate(mode === 'paused' ? 'running' : 'paused')}
          >
            {mode === 'paused' ? 'Resume' : 'Pause'}
          </Btn>
          <Btn
            variant={mode === 'stopped' ? 'danger' : 'secondary'}
            size="small"
            disabled={modeMutation.isPending}
            onClick={() => modeMutation.mutate(mode === 'stopped' ? 'running' : 'stopped')}
          >
            {mode === 'stopped' ? 'Resume' : 'Stop'}
          </Btn>
        </div>
      </aside>

      <main className="main-panel">{screen}</main>
    </div>
  )
}

export default App
