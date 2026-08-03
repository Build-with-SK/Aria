/**
 * auth/AuthContext.jsx
 * Who is signed in, and what they may see.
 *
 * The backend is the only authority here. This context caches the answer from
 * /api/auth/me so the UI can arrange itself, but nothing is enforced in the
 * browser — hiding a nav item is a courtesy, not a control. Every owner-only
 * route is refused server-side whether or not the client asks nicely.
 */
import React, { createContext, useCallback, useContext, useEffect, useState } from 'react'
import axios from 'axios'

const Ctx = createContext({
  ready: false, authenticated: false, owner: false, role: 'anon', user: null,
  refresh: () => {}, logout: () => {},
})

export const useAuth = () => useContext(Ctx)

export function AuthProvider({ children }) {
  const [state, setState] = useState({
    ready: false, authenticated: false, owner: false, role: 'anon', user: null,
  })

  const refresh = useCallback(async () => {
    try {
      const { data } = await axios.get('/api/auth/me')
      setState({
        ready: true,
        authenticated: !!data.authenticated,
        owner: !!data.owner,
        role: data.role || 'anon',
        user: data.authenticated ? data : null,
      })
    } catch {
      // A dead API must not masquerade as a signed-in session. Fail closed:
      // unreachable means unauthenticated, and the login screen says so.
      setState({ ready: true, authenticated: false, owner: false, role: 'anon', user: null })
    }
  }, [])

  const logout = useCallback(async () => {
    try { await axios.post('/api/auth/logout') } catch { /* clearing anyway */ }
    setState({ ready: true, authenticated: false, owner: false, role: 'anon', user: null })
    window.location.href = '/login'
  }, [])

  useEffect(() => { refresh() }, [refresh])

  // A 401 from anywhere means the session died mid-session — expired cookie,
  // restarted server, revoked access. Bounce to login rather than letting the
  // page quietly fill with empty panels.
  useEffect(() => {
    const id = axios.interceptors.response.use(
      r => r,
      err => {
        if (err?.response?.status === 401 && !location.pathname.startsWith('/login')) {
          setState(s => ({ ...s, authenticated: false, owner: false, role: 'anon', user: null }))
        }
        return Promise.reject(err)
      })
    return () => axios.interceptors.response.eject(id)
  }, [])

  return <Ctx.Provider value={{ ...state, refresh, logout }}>{children}</Ctx.Provider>
}
