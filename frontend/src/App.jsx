import { useState, useEffect, useRef, useCallback } from 'react'
import { newSession, sendMessage } from './api.js'

const INTENT_META = {
  1: { label: '行情数据', cls: 'asset', icon: '📈' },
  2: { label: '金融知识', cls: 'know',  icon: '📚' },
  3: { label: '日常对话', cls: 'gen',   icon: '💬' },
}

const EXAMPLES = [
  '阿里巴巴最近7天的走势如何？',
  '苹果公司当前股价是多少？',
  '什么是市盈率？',
  '收入和净利润有什么区别？',
  '腾讯最近30天的涨跌情况',
  '什么是ETF基金？',
]

function IntentBadge({ intent }) {
  const m = INTENT_META[intent]
  if (!m) return null
  return (
    <span className={`intent-badge ${m.cls}`}>
      {m.icon} {m.label}
    </span>
  )
}

function PctValue({ value }) {
  if (!value) return null
  const isUp = value.startsWith('+')
  const isDown = value.startsWith('-')
  return <span className={`data-value ${isUp ? 'up' : isDown ? 'down' : ''}`}>{value}</span>
}

function LinkList({ links, header }) {
  if (!links || links.length === 0) return null
  return (
    <div className="news-list">
      <div className="news-header">{header}</div>
      {links.map((n, i) => (
        <div key={i} className="news-item">
          {n.date && <span className="news-date">{n.date}</span>}
          {n.url ? (
            <a href={n.url} target="_blank" rel="noreferrer" className="news-title-link">
              {n.title} ↗
            </a>
          ) : (
            <span className="news-title-plain">{n.title}</span>
          )}
        </div>
      ))}
    </div>
  )
}

function DataCard({ data }) {
  if (!data || data.error || !data.price) return null
  const id = data.intraday

  return (
    <div className="data-card">
      {/* 代码 + 公司名 */}
      <div className="data-item">
        <span className="data-label">股票代码</span>
        <span className="data-value">{data.ticker}</span>
      </div>
      {data.company && data.company !== data.ticker && (
        <div className="data-item">
          <span className="data-label">公司</span>
          <span className="data-value">{data.company}</span>
        </div>
      )}

      {/* 今日实时行情 */}
      {id ? (
        <>
          <div className="data-item">
            <span className="data-label">当前价格</span>
            <span className="data-value">{data.price} {data.currency}</span>
          </div>
          <div className="data-item">
            <span className="data-label">今日开盘</span>
            <span className="data-value">{id.open} {data.currency}</span>
          </div>
          <div className="data-item">
            <span className="data-label">今日高/低</span>
            <span className="data-value">{id.high} / {id.low}</span>
          </div>
          <div className="data-item">
            <span className="data-label">较开盘</span>
            <PctValue value={id.change_from_open} />
          </div>
          <div className="data-item">
            <span className="data-label">较昨收</span>
            <PctValue value={id.change_from_prev_close} />
          </div>
        </>
      ) : (
        <>
          <div className="data-item">
            <span className="data-label">最新价格</span>
            <span className="data-value">{data.price} {data.currency}</span>
          </div>
          <div className="data-item">
            <span className="data-label">{data.period} 涨跌</span>
            <PctValue value={data.change} />
          </div>
          <div className="data-item">
            <span className="data-label">趋势</span>
            <span className="data-value">{data.trend}</span>
          </div>
        </>
      )}

      {/* 外部链接 */}
      <div className="data-links">
        {data.yahoo_url && (
          <a href={data.yahoo_url} target="_blank" rel="noreferrer" className="data-link">
            Yahoo Finance ↗
          </a>
        )}
        {data.robinhood_url && (
          <a href={data.robinhood_url} target="_blank" rel="noreferrer" className="data-link">
            Robinhood ↗
          </a>
        )}
      </div>

      {/* 相关新闻 */}
      {data.news && data.news.length > 0 && (
        <div className="news-list">
          <div className="news-header">相关新闻</div>
          {data.news.map((n, i) => (
            <div key={i} className="news-item">
              {n.date && <span className="news-date">{n.date}</span>}
              {n.url ? (
                <a href={n.url} target="_blank" rel="noreferrer" className="news-title-link">
                  {n.title} ↗
                </a>
              ) : (
                <span className="news-title-plain">{n.title}</span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function Message({ msg }) {
  const isUser = msg.role === 'user'
  return (
    <div className={`msg-row ${isUser ? 'user' : 'bot'}`}>
      <div className="avatar">{isUser ? '👤' : '🤖'}</div>
      <div className="bubble-wrap">
        {!isUser && msg.intent && <IntentBadge intent={msg.intent} />}
        <div className="bubble">{msg.content}</div>
        {!isUser && msg.data && <DataCard data={msg.data} />}
        {!isUser && msg.data?.web_links?.length > 0 && (
          <LinkList
            links={msg.data.web_links}
            header={msg.data.source === 'financial_report_api' ? '财报原文' : '参考链接'}
          />
        )}
      </div>
    </div>
  )
}

function TypingIndicator() {
  return (
    <div className="msg-row bot">
      <div className="avatar">🤖</div>
      <div className="typing">
        <div className="dot" /><div className="dot" /><div className="dot" />
      </div>
    </div>
  )
}

export default function App() {
  const [sessionId, setSessionId] = useState(null)
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const bottomRef = useRef(null)
  const textareaRef = useRef(null)

  const initSession = useCallback(async () => {
    try {
      const { session_id } = await newSession()
      setSessionId(session_id)
      setMessages([])
    } catch (e) {
      console.error(e)
    }
  }, [])

  useEffect(() => { initSession() }, [initSession])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  const send = useCallback(async (text) => {
    const msg = text.trim()
    if (!msg || loading || !sessionId) return

    setMessages(prev => [...prev, { role: 'user', content: msg }])
    setInput('')
    setLoading(true)

    try {
      const res = await sendMessage(sessionId, msg)
      setMessages(prev => [
        ...prev,
        { role: 'bot', content: res.response, intent: res.intent, data: res.data },
      ])
    } catch (e) {
      setMessages(prev => [
        ...prev,
        { role: 'bot', content: '请求失败，请检查后端服务是否运行。', intent: 3 },
      ])
    } finally {
      setLoading(false)
      setTimeout(() => textareaRef.current?.focus(), 50)
    }
  }, [loading, sessionId])

  const handleKey = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send(input)
    }
  }

  const handleNewSession = async () => {
    if (loading) return
    await initSession()
    textareaRef.current?.focus()
  }

  return (
    <div className="app">
      {/* Header */}
      <header className="header">
        <div className="header-title">
          <span className="icon">📊</span>
          <div>
            金融资产问答系统
            <div className="header-subtitle">Financial Asset QA · Powered by Gemini</div>
          </div>
        </div>
        <button className="btn-new" onClick={handleNewSession} disabled={loading}>
          新对话
        </button>
      </header>

      {/* Messages */}
      <div className="messages">
        {messages.length === 0 ? (
          <div className="empty-state">
            <div className="big-icon">📈</div>
            <h2>欢迎使用金融资产问答系统</h2>
            <p>
              您可以询问任意股票的实时行情与走势分析，<br />
              也可以提问金融概念、财报解读等知识性问题。
            </p>
            <div className="examples">
              {EXAMPLES.map(ex => (
                <button
                  key={ex}
                  className="example-chip"
                  onClick={() => send(ex)}
                  disabled={loading}
                >
                  {ex}
                </button>
              ))}
            </div>
          </div>
        ) : (
          messages.map((msg, i) => <Message key={i} msg={msg} />)
        )}
        {loading && <TypingIndicator />}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="input-bar">
        <textarea
          ref={textareaRef}
          value={input}
          onChange={e => {
            setInput(e.target.value)
            e.target.style.height = 'auto'
            e.target.style.height = Math.min(e.target.scrollHeight, 140) + 'px'
          }}
          onKeyDown={handleKey}
          placeholder="输入问题，按 Enter 发送（Shift+Enter 换行）…"
          disabled={loading}
          rows={1}
        />
        <button
          className="btn-send"
          onClick={() => send(input)}
          disabled={loading || !input.trim()}
          title="发送"
        >
          ➤
        </button>
      </div>
    </div>
  )
}
