import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createProfile, deleteProfile, getProfiles } from '../api'
import { Btn } from '../components/Btn'
import { IcoPlus } from '../components/icons'

function parseArgs(raw: string): string[] | null {
  const trimmed = raw.trim()
  if (!trimmed) {
    return null
  }

  const parsed = JSON.parse(trimmed) as unknown
  if (!Array.isArray(parsed) || !parsed.every((entry) => typeof entry === 'string')) {
    throw new Error('ffmpeg_args must be a JSON array of strings')
  }
  return parsed
}

function formatBytes(value: number | null): string {
  if (value === null) {
    return 'No target'
  }
  return `${Math.round(value / (1024 * 1024))} MB`
}

export function ProfilesScreen() {
  const queryClient = useQueryClient()
  const [id, setId] = useState('')
  const [name, setName] = useState('')
  const [targetSizeBytes, setTargetSizeBytes] = useState('')
  const [ffmpegArgs, setFfmpegArgs] = useState('')
  const [formError, setFormError] = useState<string | null>(null)

  const profilesQuery = useQuery({
    queryKey: ['profiles'],
    queryFn: getProfiles,
  })

  const createMutation = useMutation({
    mutationFn: createProfile,
    onSuccess: () => {
      setId('')
      setName('')
      setTargetSizeBytes('')
      setFfmpegArgs('')
      setFormError(null)
      void queryClient.invalidateQueries({ queryKey: ['profiles'] })
    },
  })

  const deleteMutation = useMutation({
    mutationFn: deleteProfile,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['profiles'] })
    },
  })

  return (
    <section className="screen">
      <header className="screen-header">
        <div>
          <div className="section-label">Profiles</div>
          <h2 className="screen-title">Transcode profiles</h2>
          <p className="screen-subtitle">Manage the presets referenced by subscriptions and assets.</p>
        </div>
      </header>

      <div className="split-layout">
        <div className="card">
          <div className="card-body stack">
            <div>
              <div className="section-label">Create</div>
              <h3 className="list-row__title">New profile</h3>
            </div>

            <form
              className="form-grid"
              onSubmit={(event) => {
                event.preventDefault()
                try {
                  createMutation.mutate({
                    id: id.trim(),
                    name: name.trim(),
                    target_size_bytes: targetSizeBytes.trim() ? Number(targetSizeBytes) : null,
                    ffmpeg_args: parseArgs(ffmpegArgs),
                  })
                } catch (error) {
                  setFormError(error instanceof Error ? error.message : 'Invalid ffmpeg args')
                }
              }}
            >
              <div className="form-field">
                <label htmlFor="profile-id">Profile ID</label>
                <input id="profile-id" className="surface-input mono" value={id} onChange={(event) => setId(event.target.value)} />
              </div>
              <div className="form-field">
                <label htmlFor="profile-name">Name</label>
                <input id="profile-name" className="surface-input" value={name} onChange={(event) => setName(event.target.value)} />
              </div>
              <div className="form-field">
                <label htmlFor="profile-target">Target size bytes</label>
                <input
                  id="profile-target"
                  className="surface-input mono"
                  value={targetSizeBytes}
                  onChange={(event) => setTargetSizeBytes(event.target.value)}
                  placeholder="2147483648"
                />
              </div>
              <div className="form-field">
                <label htmlFor="profile-ffmpeg">ffmpeg args JSON</label>
                <textarea
                  id="profile-ffmpeg"
                  className="surface-textarea mono"
                  value={ffmpegArgs}
                  onChange={(event) => setFfmpegArgs(event.target.value)}
                  placeholder='["-c:v", "libx265", "-crf", "23"]'
                />
              </div>
              <Btn type="submit" variant="primary" disabled={createMutation.isPending || !id.trim() || !name.trim()}>
                <IcoPlus />
                Create profile
              </Btn>
            </form>

            {formError ? <div className="notice notice--error">{formError}</div> : null}
            {createMutation.isError ? <div className="notice notice--error">{createMutation.error.message}</div> : null}
          </div>
        </div>

        <div className="card">
          <div className="list">
            {profilesQuery.isLoading ? <div className="notice">Loading profiles…</div> : null}
            {profilesQuery.isError ? <div className="notice notice--error">{profilesQuery.error.message}</div> : null}
            {profilesQuery.data?.map((profile) => (
              <div key={profile.id} className="list-row">
                <div className="stack" style={{ gap: 6, minWidth: 0 }}>
                  <div>
                    <h3 className="list-row__title">{profile.name}</h3>
                    <p className="list-row__meta mono">{profile.id}</p>
                  </div>
                  <div className="inline-cluster muted">
                    <span className="badge">{formatBytes(profile.target_size_bytes)}</span>
                    <span className="mono">{profile.ffmpeg_args?.join(' ') ?? 'passthrough'}</span>
                  </div>
                </div>

                <Btn
                  variant="danger"
                  size="small"
                  disabled={deleteMutation.isPending}
                  onClick={() => deleteMutation.mutate(profile.id)}
                >
                  Delete
                </Btn>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  )
}
