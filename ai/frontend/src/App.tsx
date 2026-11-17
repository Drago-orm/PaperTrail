import { useEffect, useState } from 'react'

type Health = {
  status: 'ok'
  model: {
    configured: boolean
    missing: string[]
    errors: string[]
  }
}

type ServiceState =
  | { status: 'loading' }
  | { status: 'ready'; health: Health }
  | { status: 'error'; message: string }

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item: unknown) => typeof item === 'string')
}

function isHealth(value: unknown): value is Health {
  return (
    isRecord(value) &&
    value.status === 'ok' &&
    isRecord(value.model) &&
    typeof value.model.configured === 'boolean' &&
    isStringArray(value.model.missing) &&
    isStringArray(value.model.errors)
  )
}

export default function App() {
  const [attempt, setAttempt] = useState(0)
  const [service, setService] = useState<ServiceState>({ status: 'loading' })

  useEffect(() => {
    const controller = new AbortController()
    const timeout = window.setTimeout(() => controller.abort(), 10_000)
    let active = true

    async function checkHealth() {
      setService({ status: 'loading' })
      try {
        const response = await fetch('/api/health', { signal: controller.signal })
        if (!response.ok) throw new Error(`服务返回 HTTP ${response.status}`)
        const data: unknown = await response.json()
        if (!isHealth(data)) throw new Error('健康接口返回格式不符合约定')
        if (active) setService({ status: 'ready', health: data })
      } catch (error: unknown) {
        if (active) {
          const message = controller.signal.aborted
            ? '连接超过 10 秒，请确认后端服务已启动。'
            : error instanceof Error
              ? error.message
              : '服务请求失败，未获得有效响应。'
          setService({ status: 'error', message })
        }
      } finally {
        window.clearTimeout(timeout)
      }
    }

    void checkHealth()
    return () => {
      active = false
      controller.abort()
      window.clearTimeout(timeout)
    }
  }, [attempt])

  const model = service.status === 'ready' ? service.health.model : null

  return (
    <div className="workspace">
      <aside className="sidebar" aria-label="工作空间导航">
        <a className="brand" href="/" aria-label="研迹 PaperTrail 首页">
          <span className="brand-mark" aria-hidden="true">研</span>
          <span><strong>研迹</strong><small>PaperTrail</small></span>
        </a>
        <p className="nav-label">工作空间</p>
        <div className="nav-current" aria-current="page">文献库</div>
        <p className="sidebar-note">本地论文研读空间</p>
      </aside>

      <main>
        <header className="page-header">
          <div>
            <p className="eyebrow">PAPERTRAIL / LIBRARY</p>
            <h1>文献库</h1>
            <p className="muted">阅读的起点，研究线索的归处。</p>
          </div>
          <span className="local-badge">本地运行</span>
        </header>

        <div className="content-grid">
          <section className="library-panel" aria-labelledby="library-title">
            <div className="panel-header"><h2 id="library-title">我的文献</h2></div>
            <div className="empty-library">
              <svg viewBox="0 0 48 48" fill="none" aria-hidden="true">
                <rect x="15" y="8" width="24" height="32" rx="3" />
                <path d="M10 13H8v26a5 5 0 0 0 5 5h20M21 17h12M21 23h12M21 29h7" />
              </svg>
              <h3>等待导入文献</h3>
              <p>此页面尚未接入文献列表和上传操作。</p>
              <p className="muted">先确认服务状态，开启你的论文研读空间。</p>
            </div>
          </section>

          <section className="service-panel" aria-labelledby="service-title" aria-busy={service.status === 'loading'}>
            <div className="panel-header">
              <h2 id="service-title">服务状态</h2>
              <button type="button" disabled={service.status === 'loading'} onClick={() => setAttempt((value) => value + 1)}>
                {service.status === 'loading' ? '检查中…' : '重新检查'}
              </button>
            </div>
            <div className="service-content" aria-live="polite">
              <div className="status-row">
                <span>后端服务</span>
                <span className={`status ${service.status}`}>
                  {service.status === 'ready' ? '连接正常' : service.status === 'error' ? '连接失败' : '正在连接'}
                </span>
              </div>
              {service.status === 'error' && <p className="error-message" role="alert">{service.message}</p>}
              <div className="model-status">
                <h3>模型配置</h3>
                {model ? (
                  <>
                    <p className={model.configured ? 'configured' : 'configuration-warning'}>
                      {model.configured ? '配置已就绪' : '配置缺失或无效'}
                    </p>
                    {model.missing.length > 0 && (
                      <><p className="muted">尚未配置：</p><ul>{model.missing.map((name) => <li key={name}><code>{name}</code></li>)}</ul></>
                    )}
                    {model.errors.length > 0 && <ul className="error-message">{model.errors.map((message) => <li key={message}>{message}</li>)}</ul>}
                    <p className="muted">
                      {model.configured
                        ? '当前版本尚未提供 AI 分析操作。'
                        : '请在后端环境中补全配置，重启服务后重新检查。'}
                    </p>
                  </>
                ) : <p className="muted">{service.status === 'loading' ? '等待服务响应…' : '无法获取模型配置状态。'}</p>}
              </div>
            </div>
          </section>
        </div>
      </main>
    </div>
  )
}
