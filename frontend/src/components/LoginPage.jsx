import { Activity, ArrowRight, CircleDot, ShieldCheck } from 'lucide-react'

const authHelpUrl = import.meta.env.VITE_AUTH_HELP_URL || ''

export default function LoginPage({ oidcEnabled, loading = false, error = '', onSignIn, onDemoAccess }) {
  return (
    <main className="login-shell">
      <section className="login-identity" aria-label="Compunnel FieldNote">
        <a className="login-brand" href="#overview" aria-label="Compunnel FieldNote">
          <span className="login-brand-mark"><Activity size={20} strokeWidth={2.5} /></span>
          <span>Compunnel <b>FieldNote</b></span>
        </a>
        <div className="login-signal" aria-hidden="true">
          <span /><span /><span /><span /><span /><span /><span /><span /><span /><span /><span /><span />
        </div>
        <div className="login-identity-copy">
          <p className="login-eyebrow">CONDITION INTELLIGENCE</p>
          <h1>Field operations,<br />with evidence.</h1>
          <p>Maintenance decisions grounded in equipment data, approved references, and human review.</p>
        </div>
        <div className="login-identity-footer"><CircleDot size={13} /> OPERATIONAL DECISION SUPPORT</div>
      </section>

      <section className="login-access">
        <div className="login-access-content">
          <div className="login-access-icon"><ShieldCheck size={20} /></div>
          <p className="login-eyebrow">WORKSPACE ACCESS</p>
          <h2>Sign in</h2>
          <p className="login-access-copy">
            {oidcEnabled
              ? 'Continue with your organization account to access FieldNote.'
              : 'Organization sign-in is not configured for this environment.'}
          </p>

          {error && <p className="login-error" role="alert">{error}</p>}

          {oidcEnabled ? (
            <button className="button button-primary login-submit" type="button" onClick={onSignIn} disabled={loading}>
              {loading ? 'Connecting…' : 'Continue with organization account'}
              {!loading && <ArrowRight size={16} />}
            </button>
          ) : (
            <button className="button button-primary login-submit" type="button" onClick={onDemoAccess} disabled={loading}>
              Continue to local demo
              <ArrowRight size={16} />
            </button>
          )}

          {!oidcEnabled && <p className="login-demo-note">Local demo access uses an unverified development identity. It is not a production sign-in method.</p>}
          <div className="login-help">
            {authHelpUrl
              ? <a href={authHelpUrl} target="_blank" rel="noreferrer">Need access? Get sign-in help</a>
              : <span>Need access? Contact your IT administrator.</span>}
          </div>
          <div className="login-access-footer"><span /> HUMAN APPROVAL REQUIRED FOR DISPATCH</div>
        </div>
      </section>
    </main>
  )
}