import { useState, useEffect, useCallback } from 'react'
import axios from 'axios'

export function useApprovalQueue(pollInterval = 4000) {
  const [data, setData]     = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError]   = useState(null)

  const fetch = useCallback(async () => {
    try {
      const r = await axios.get('/api/execute/queue')
      setData(r.data)
      setError(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetch()
    const id = setInterval(fetch, pollInterval)
    return () => clearInterval(id)
  }, [fetch, pollInterval])

  return { data, loading, error, refetch: fetch }
}

export function useBrokerStatus() {
  const [data, setData]     = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    axios.get('/api/execute/brokers')
      .then(r => setData(r.data))
      .catch(() => setData(null))
      .finally(() => setLoading(false))
  }, [])

  return { data, loading }
}

export function useLivePositions() {
  const [data, setData] = useState(null)
  useEffect(() => {
    axios.get('/api/execute/positions').then(r => setData(r.data)).catch(() => {})
  }, [])
  return data
}

export async function approveAndExecute(trade_id) {
  const r = await axios.post(`/api/execute/approve/${trade_id}`)
  return r.data
}

export async function rejectTrade(trade_id, reason = '') {
  const r = await axios.post(`/api/execute/reject/${trade_id}?reason=${encodeURIComponent(reason)}`)
  return r.data
}

export async function cancelTrade(trade_id) {
  const r = await axios.post(`/api/execute/cancel/${trade_id}`)
  return r.data
}

export async function proposeTrade(body) {
  const r = await axios.post('/api/execute/propose', body)
  return r.data
}
