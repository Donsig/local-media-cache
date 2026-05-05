import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Btn } from '../components/Btn'

const TOKEN_KEY = 'ui_token'

export function SettingsScreen() {
  const queryClient = useQueryClient()
  const [token, setToken] = useState('')
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    setToken(localStorage.getItem(TOKEN_KEY) ?? '')
  }, [])

  return (
    <section className="screen">
      <header className="screen-header">
        <div>
          <div className="section-label">Settings</div>
          <h2 className="screen-title">UI access token</h2>
          <p className="screen-subtitle">Store the UI bearer token in localStorage for all /api requests.</p>
        </div>
      </header>

      <div className="card" style={{ maxWidth: 720 }}>
        <div className="card-body stack">
          <div className="form-field">
            <label htmlFor="ui-token">ui_token</label>
            <input
              id="ui-token"
              className="surface-input mono"
              value={token}
              onChange={(event) => {
                setSaved(false)
                setToken(event.target.value)
              }}
              placeholder="Paste bearer token"
            />
          </div>

          <div className="inline-cluster">
            <Btn
              variant="primary"
              onClick={() => {
                localStorage.setItem(TOKEN_KEY, token)
                setSaved(true)
                void queryClient.invalidateQueries()
              }}
            >
              Save token
            </Btn>
            {saved ? <span className="muted">Saved to localStorage.</span> : null}
          </div>

          <div className="notice">
            <div className="section-label">Server URL</div>
            <div className="mono">{window.location.origin}</div>
            <p className="screen-subtitle">Dev uses the Vite proxy for /api. Production serves the SPA from FastAPI.</p>
          </div>
        </div>
      </div>
    </section>
  )
}
