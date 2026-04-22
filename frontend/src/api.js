const BASE = ''  // Vite proxy forwards to http://localhost:8000

export async function newSession() {
  const res = await fetch(`${BASE}/new_session`, { method: 'POST' })
  if (!res.ok) throw new Error('Failed to create session')
  return res.json()
}

export async function sendMessage(sessionId, message) {
  const res = await fetch(`${BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, message }),
  })
  if (!res.ok) throw new Error('Request failed')
  return res.json()
}
