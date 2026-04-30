const ADMIN_TOKEN_STORAGE_KEY = 'admin_api_token'
const ADMIN_WS_PROTOCOL = 'airr-admin'
const ADMIN_WS_TOKEN_PREFIX = 'airr-admin-token.'

function toBase64Url(value: string): string {
  if (typeof window === 'undefined') {
    return ''
  }

  const bytes = new TextEncoder().encode(value)
  let binary = ''
  for (const byte of bytes) {
    binary += String.fromCharCode(byte)
  }

  return window.btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '')
}

export function getStoredAdminToken(): string {
  if (typeof window === 'undefined') {
    return ''
  }

  return (window.localStorage.getItem(ADMIN_TOKEN_STORAGE_KEY) || '').trim()
}

export function setStoredAdminToken(token: string): void {
  if (typeof window === 'undefined') {
    return
  }

  const normalized = token.trim()
  if (normalized) {
    window.localStorage.setItem(ADMIN_TOKEN_STORAGE_KEY, normalized)
    return
  }

  window.localStorage.removeItem(ADMIN_TOKEN_STORAGE_KEY)
}

export function clearStoredAdminToken(): void {
  if (typeof window === 'undefined') {
    return
  }

  window.localStorage.removeItem(ADMIN_TOKEN_STORAGE_KEY)
}

export function buildAdminAuthHeaders(headers: Record<string, string> = {}): Record<string, string> {
  const token = getStoredAdminToken()
  if (!token) {
    return headers
  }

  return {
    ...headers,
    Authorization: `Bearer ${token}`,
  }
}

export function openAdminWebSocket(url: string): WebSocket {
  const token = getStoredAdminToken()
  if (!token) {
    return new WebSocket(url)
  }

  return new WebSocket(url, [ADMIN_WS_PROTOCOL, `${ADMIN_WS_TOKEN_PREFIX}${toBase64Url(token)}`])
}