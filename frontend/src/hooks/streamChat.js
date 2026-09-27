/**
 * hooks/streamChat.js
 * Read a server-sent-event reply from POST /api/chat/stream.
 *
 * fetch() rather than EventSource: EventSource cannot send a body, and cannot
 * send the Origin that the backend's CSRF guard expects on a write. The session
 * cookie goes by itself (same origin).
 *
 * Events, as the backend defines them (backend/main.py, aria_chat_stream):
 *   ack · status · token · reset · done · error
 * `done` is the authoritative reply; the caller replaces its draft with it.
 *
 * Throws an Error carrying `.status` if the stream could not START (the caller
 * may fall back to the blocking route), or `.abstained` if ARIA answered with
 * an `error` event — which is a real answer ("I can't reach my brain") and
 * must not be retried against another route.
 */
export async function streamChat(url, body, on = {}) {
  const r = await fetch(url, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok || !r.body) {
    let detail = r.statusText
    try { detail = (await r.json())?.detail || detail } catch { /* not JSON */ }
    throw Object.assign(new Error(typeof detail === 'string' ? detail : JSON.stringify(detail)),
      { status: r.status })
  }

  const reader = r.body.getReader()
  const dec = new TextDecoder()
  let buf = ''
  let done = null
  for (;;) {
    const { value, done: end } = await reader.read()
    if (end) break
    buf += dec.decode(value, { stream: true })
    let cut
    while ((cut = buf.indexOf('\n\n')) >= 0) {
      const block = buf.slice(0, cut)
      buf = buf.slice(cut + 2)
      let ev = '', data = ''
      for (const line of block.split('\n')) {
        if (line.startsWith('event: ')) ev = line.slice(7)
        else if (line.startsWith('data: ')) data += line.slice(6)
      }                                            // ':' lines are keep-alives
      if (!ev) continue
      const d = data ? JSON.parse(data) : {}
      if (ev === 'done') done = d
      else if (ev === 'error') {
        throw Object.assign(new Error(d.detail || 'ARIA could not answer'),
          { abstained: true, status: d.status })
      } else if (on[ev]) on[ev](d)
    }
  }
  if (!done) throw Object.assign(new Error('the stream ended without a reply'), { status: 0 })
  return done
}
