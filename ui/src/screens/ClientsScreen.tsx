import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createClient, deleteClient, getClients } from '../api'
import { Btn } from '../components/Btn'
import { IcoPlus } from '../components/icons'
import { ProgressBar } from '../components/ProgressBar'

function formatBytes(value: number | null): string {
  if (value === null) {
    return 'Not set'
  }

  if (value < 1024) {
    return `${value} B`
  }

  const units = ['KB', 'MB', 'GB', 'TB']
  let size = value
  let unitIndex = -1
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024
    unitIndex += 1
  }

  return `${size.toFixed(size >= 10 ? 0 : 1)} ${units[unitIndex]}`
}

export function ClientsScreen() {
  const queryClient = useQueryClient()
  const [id, setId] = useState('')
  const [name, setName] = useState('')
  const [storageBudget, setStorageBudget] = useState('')
  const [lastToken, setLastToken] = useState<string | null>(null)

  const clientsQuery = useQuery({
    queryKey: ['clients'],
    queryFn: getClients,
  })

  const createMutation = useMutation({
    mutationFn: createClient,
    onSuccess: (client) => {
      setId('')
      setName('')
      setStorageBudget('')
      setLastToken(client.auth_token)
      void queryClient.invalidateQueries({ queryKey: ['clients'] })
    },
  })

  const deleteMutation = useMutation({
    mutationFn: deleteClient,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['clients'] })
    },
  })

  return (
    <section className="screen">
      <header className="screen-header">
        <div>
          <div className="section-label">Clients</div>
          <h2 className="screen-title">Remote clients</h2>
          <p className="screen-subtitle">Create clients, inspect their metadata, and decommission them.</p>
        </div>
      </header>

      <div className="split-layout">
        <div className="card">
          <div className="card-body stack">
            <div>
              <div className="section-label">Create</div>
              <h3 className="list-row__title">New client</h3>
            </div>

            <form
              className="form-grid"
              onSubmit={(event) => {
                event.preventDefault()
                createMutation.mutate({
                  id: id.trim(),
                  name: name.trim(),
                  storage_budget_bytes: storageBudget.trim() ? Number(storageBudget) : null,
                })
              }}
            >
              <div className="form-field">
                <label htmlFor="client-id">Client ID</label>
                <input id="client-id" className="surface-input mono" value={id} onChange={(event) => setId(event.target.value)} />
              </div>
              <div className="form-field">
                <label htmlFor="client-name">Name</label>
                <input id="client-name" className="surface-input" value={name} onChange={(event) => setName(event.target.value)} />
              </div>
              <div className="form-field">
                <label htmlFor="client-budget">Storage budget bytes</label>
                <input
                  id="client-budget"
                  className="surface-input mono"
                  value={storageBudget}
                  onChange={(event) => setStorageBudget(event.target.value)}
                  placeholder="268435456000"
                />
              </div>
              <Btn type="submit" variant="primary" disabled={createMutation.isPending || !id.trim() || !name.trim()}>
                <IcoPlus />
                Create client
              </Btn>
            </form>

            {lastToken ? (
              <div className="notice">
                <div className="section-label">Auth token</div>
                <div className="mono">{lastToken}</div>
              </div>
            ) : null}

            {createMutation.isError ? <div className="notice notice--error">{createMutation.error.message}</div> : null}
          </div>
        </div>

        <div className="card">
          <div className="list">
            {clientsQuery.isLoading ? <div className="notice">Loading clients…</div> : null}
            {clientsQuery.isError ? <div className="notice notice--error">{clientsQuery.error.message}</div> : null}
            {clientsQuery.data?.map((client) => (
              <div key={client.id} className="list-row">
                <div className="stack" style={{ gap: 8, minWidth: 0 }}>
                  <div>
                    <h3 className="list-row__title">{client.name}</h3>
                    <p className="list-row__meta mono">
                      {client.id} · last seen {client.last_seen ?? 'never'}
                    </p>
                  </div>
                  <ProgressBar
                    value={client.storage_budget_bytes ? 0 : 0}
                    label={`Storage budget: ${formatBytes(client.storage_budget_bytes)}`}
                  />
                </div>

                <div className="inline-cluster">
                  {client.decommissioning ? <span className="badge">decommissioning</span> : null}
                  <Btn
                    variant="danger"
                    size="small"
                    disabled={deleteMutation.isPending}
                    onClick={() => deleteMutation.mutate(client.id)}
                  >
                    Delete
                  </Btn>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  )
}
