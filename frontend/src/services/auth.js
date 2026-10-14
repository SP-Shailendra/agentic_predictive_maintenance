import { UserManager, WebStorageStateStore } from 'oidc-client-ts'

const authority = import.meta.env.VITE_OIDC_AUTHORITY || ''
const clientId = import.meta.env.VITE_OIDC_CLIENT_ID || ''
const redirectUri = import.meta.env.VITE_OIDC_REDIRECT_URI || window.location.origin
const rolesClaim = import.meta.env.VITE_OIDC_ROLES_CLAIM || 'roles'

export const oidcEnabled = Boolean(authority && clientId)

const userManager = oidcEnabled
  ? new UserManager({
      authority,
      client_id: clientId,
      redirect_uri: redirectUri,
      post_logout_redirect_uri: redirectUri,
      response_type: 'code',
      scope: 'openid profile email offline_access',
      automaticSilentRenew: true,
      silent_redirect_uri: redirectUri,
      loadUserInfo: false,
      userStore: new WebStorageStateStore({ store: window.sessionStorage }),
    })
  : null

export async function initializeAuth() {
  if (!userManager) return { enabled: false, user: null }
  const params = new URLSearchParams(window.location.search)
  let user
  if (params.has('code') || params.has('error')) {
    if (window.self !== window.top) {
      await userManager.signinSilentCallback()
      user = await userManager.getUser()
    } else {
      user = await userManager.signinRedirectCallback()
    }
    window.history.replaceState({}, document.title, `${window.location.pathname}${window.location.hash}`)
  } else if (params.has('state')) {
    await userManager.signoutRedirectCallback()
    user = null
  } else {
    user = await userManager.getUser()
  }
  return { enabled: true, user: user && !user.expired ? user : null }
}

export async function signIn() {
  if (!userManager) return
  await userManager.signinRedirect()
}

export async function signOut() {
  if (!userManager) return
  await userManager.signoutRedirect()
}

export async function getAccessToken() {
  if (!userManager) return null
  const user = await userManager.getUser()
  if (!user || user.expired) throw new Error('Your sign-in session expired. Sign in again to continue.')
  return user.access_token
}

export function getUserRoles(user) {
  if (!user?.profile) return []
  let value = user.profile
  for (const segment of rolesClaim.split('.')) value = value?.[segment]
  const roles = Array.isArray(value) ? value : typeof value === 'string' ? value.split(/[ ,]+/) : []
  return [...new Set(roles.filter((role) => typeof role === 'string').map((role) => role.toLowerCase()))]
}

export function getUserDisplayName(user) {
  return user?.profile?.name || user?.profile?.preferred_username || user?.profile?.email || user?.profile?.sub || ''
}