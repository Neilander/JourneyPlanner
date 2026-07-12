'use client'

import { useEffect, useState } from 'react'

const API_BASE = 'https://api.neiland.xyz'

interface Trip {
  id: number
  city: string
  days: number
  preference: string
  bundle_text: string
  budget_text: string
  created_at: string
}

// ── 文本解析 ─────────────────────────────────────────────────────
type Block =
  | { type: 'plan_title'; text: string }
  | { type: 'plan_style'; text: string }
  | { type: 'day_header'; day: number; title: string }
  | { type: 'time_block'; emoji: string; label: string; content: string }
  | { type: 'transport'; text: string }
  | { type: 'tip'; text: string }
  | { type: 'bullet'; text: string }
  | { type: 'text'; text: string }

function stripBold(s: string) {
  return s.replace(/\*\*(.*?)\*\*/g, '$1').replace(/\*(.*?)\*/g, '$1').trim()
}

function parseBundle(raw: string): Block[][] {
  const plans: Block[][] = []
  let current: Block[] = []

  for (const rawLine of raw.split('\n')) {
    const line = rawLine.trim()
    if (!line) continue

    // 两个方案之间的分隔线
    if (/^---+$/.test(line)) {
      if (current.length > 0) { plans.push(current); current = [] }
      continue
    }

    // 方案标题 【方案一：xxx】
    const planTitleM = line.match(/^【(方案.+?)】$/)
    if (planTitleM) {
      current.push({ type: 'plan_title', text: planTitleM[1] }); continue
    }

    // 方案风格描述（方案标题后紧跟的一行说明）
    if (current.length > 0 && current[current.length - 1].type === 'plan_title') {
      current.push({ type: 'plan_style', text: stripBold(line) }); continue
    }

    // Day X: 标题
    const dayM = line.match(/^\*?\*?Day\s*(\d+)[：:]\s*(.*?)\*?\*?$/)
    if (dayM) {
      current.push({ type: 'day_header', day: parseInt(dayM[1]), title: stripBold(dayM[2]) }); continue
    }

    // 时间块  - **上午/下午/晚上 xxx**
    const timeM = line.match(/^-\s*\*\*?(上午|下午|晚上|早上|中午)(.*?)\*?\*?$/)
    if (timeM) {
      const map: Record<string, string> = { 上午: '🌅', 早上: '🌅', 中午: '☀️', 下午: '🌤️', 晚上: '🌙' }
      current.push({ type: 'time_block', emoji: map[timeM[1]] || '⏰', label: timeM[1], content: stripBold(timeM[2]) }); continue
    }

    // 交通行
    const transM = line.match(/^[\s-]*\*?交通[：:]\s*(.*?)\*?$/)
    if (transM) {
      current.push({ type: 'transport', text: stripBold(transM[1]) }); continue
    }

    // 建议/注意行
    const tipM = line.match(/^[\s-]*\*?(建议|注意|Tips?|温馨提示)[：:]\s*(.*?)\*?$/i)
    if (tipM) {
      current.push({ type: 'tip', text: stripBold(tipM[2]) }); continue
    }

    // 普通列表行
    if (line.startsWith('-') || line.startsWith('•') || line.startsWith('*')) {
      const content = stripBold(line.slice(1).trim())
      if (content) current.push({ type: 'bullet', text: content }); continue
    }

    current.push({ type: 'text', text: stripBold(line) })
  }

  if (current.length > 0) plans.push(current)
  return plans.filter(p => p.length > 0)
}

// ── Block 渲染 ────────────────────────────────────────────────────
function BlockView({ block }: { block: Block }) {
  switch (block.type) {
    case 'plan_title':
      return (
        <div className="flex items-center gap-2 mt-2 mb-1">
          <span className="text-xl">🗺️</span>
          <span className="text-lg font-bold" style={{ color: '#b45309' }}>{block.text}</span>
        </div>
      )
    case 'plan_style':
      return <p className="text-sm italic mb-3 leading-relaxed" style={{ color: '#6b7280' }}>{block.text}</p>

    case 'day_header':
      return (
        <div className="flex items-center gap-3 mt-5 mb-2">
          <div className="flex items-center justify-center w-9 h-9 rounded-full text-white font-bold text-sm shrink-0"
               style={{ background: 'linear-gradient(135deg,#f59e0b,#ef4444)' }}>
            D{block.day}
          </div>
          <span className="font-bold text-base" style={{ color: '#1f2937' }}>{block.title}</span>
        </div>
      )

    case 'time_block':
      return (
        <div className="flex items-start gap-2 mt-3 mb-1">
          <span className="text-base mt-0.5">{block.emoji}</span>
          <div>
            <span className="text-xs font-semibold uppercase tracking-wide" style={{ color: '#d97706' }}>{block.label}</span>
            {block.content && <span className="ml-1 text-sm" style={{ color: '#374151' }}>{block.content}</span>}
          </div>
        </div>
      )

    case 'transport':
      return (
        <div className="flex items-start gap-1.5 ml-4 mt-1 text-xs rounded px-2 py-1"
             style={{ color: '#2563eb', background: '#eff6ff' }}>
          <span className="shrink-0">🚌</span>
          <span className="leading-relaxed">{block.text}</span>
        </div>
      )

    case 'tip':
      return (
        <div className="flex items-start gap-1.5 ml-4 mt-1 text-xs rounded px-2 py-1"
             style={{ color: '#15803d', background: '#f0fdf4' }}>
          <span className="shrink-0">💡</span>
          <span className="leading-relaxed">{block.text}</span>
        </div>
      )

    case 'bullet':
      return (
        <div className="flex items-start gap-2 ml-4 mt-1.5 text-sm" style={{ color: '#374151' }}>
          <span className="shrink-0 mt-0.5 font-bold" style={{ color: '#f59e0b' }}>›</span>
          <span className="leading-relaxed">{block.text}</span>
        </div>
      )

    case 'text':
      return <p className="text-sm ml-1 mt-1 leading-relaxed" style={{ color: '#4b5563' }}>{block.text}</p>

    default:
      return null
  }
}

// ── 主页面 ──────────────────────────────────────────────────────
export default function TripPage() {
  const [trip, setTrip] = useState<Trip | null>(null)
  const [error, setError] = useState('')
  const [activeTab, setActiveTab] = useState(0)

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const id = params.get('id')
    if (!id) { setError('缺少行程 ID'); return }

    fetch(`${API_BASE}/api/trips/${id}`)
      .then(r => {
        if (!r.ok) throw new Error('not found')
        return r.json()
      })
      .then(setTrip)
      .catch(() => setError('找不到这份行程，可能已过期～'))
  }, [])

  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center text-sm" style={{ color: '#6b7280' }}>
        {error}
      </div>
    )
  }

  if (!trip) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center gap-3">
        <div className="text-3xl animate-pulse">🗺️</div>
        <div className="text-sm" style={{ color: '#d97706' }}>加载行程中…</div>
      </div>
    )
  }

  const plans = parseBundle(trip.bundle_text)
  const date = new Date(trip.created_at).toLocaleDateString('zh-CN', { month: 'long', day: 'numeric' })
  const budgetLines = (trip.budget_text || '').split('\n').map(l => l.trim()).filter(Boolean)

  return (
    <div className="min-h-screen" style={{ background: '#fffbeb', fontFamily: "'LXGW WenKai Screen', serif" }}>
      {/* 顶部渐变 */}
      <div className="px-4 pt-10 pb-6 text-white"
           style={{ background: 'linear-gradient(135deg,#f59e0b 0%,#ef4444 100%)' }}>
        <div className="text-xs mb-1" style={{ opacity: 0.85 }}>{date} 生成</div>
        <h1 className="text-2xl font-bold tracking-wide">📍 {trip.city} {trip.days}日行程</h1>
        {trip.preference && (
          <div className="mt-1 text-sm" style={{ opacity: 0.9 }}>偏好：{trip.preference}</div>
        )}
        <div className="mt-2 text-xs" style={{ opacity: 0.75 }}>共 {plans.length} 套方案，点击切换</div>
      </div>

      {/* 方案切换 Tab */}
      {plans.length > 1 && (
        <div className="flex gap-2 px-4 pt-4">
          {plans.map((_, i) => (
            <button
              key={i}
              onClick={() => setActiveTab(i)}
              className="flex-1 py-2 rounded-xl text-sm font-medium transition-all"
              style={{
                background: activeTab === i ? '#f59e0b' : '#fff',
                color: activeTab === i ? '#fff' : '#6b7280',
                border: activeTab === i ? 'none' : '1px solid #e5e7eb',
                boxShadow: activeTab === i ? '0 2px 8px rgba(245,158,11,0.3)' : 'none',
              }}
            >
              方案 {i + 1}
            </button>
          ))}
        </div>
      )}

      {/* 行程内容 */}
      <div className="px-4 py-4 pb-16">
        <div className="rounded-2xl px-4 py-4"
             style={{ background: '#fff', boxShadow: '0 1px 8px rgba(0,0,0,0.06)' }}>
          {(plans[activeTab] || []).map((block, i) => (
            <BlockView key={i} block={block} />
          ))}
        </div>
        {/* 预算卡片 */}
        {budgetLines.length > 0 && (
          <div className="mt-4 rounded-2xl px-4 py-4"
               style={{ background: '#fff', boxShadow: '0 1px 8px rgba(0,0,0,0.06)' }}>
            {budgetLines.map((line, i) => {
              if (line.startsWith('【') && line.endsWith('】'))
                return <div key={i} className="text-sm font-bold mb-3" style={{ color: '#b45309' }}>{line.slice(1,-1)}</div>
              if (line.startsWith('💰'))
                return <div key={i} className="mt-3 pt-3 text-sm font-bold" style={{ borderTop: '1px dashed #fde68a', color: '#92400e' }}>{line}</div>
              if (line.startsWith('💡'))
                return <div key={i} className="mt-3 text-xs rounded-lg px-3 py-2" style={{ background: '#f0fdf4', color: '#15803d' }}>{line}</div>
              if (line.startsWith('（') || line.startsWith('('))
                return <div key={i} className="text-xs ml-1 mb-1" style={{ color: '#9ca3af' }}>{line}</div>
              if (line.match(/^[🚇🏨🍜🎫🛍️]/u))
                return <div key={i} className="flex justify-between items-center mt-2 text-sm" style={{ color: '#374151' }}>
                  <span>{line.split('：')[0]}</span>
                  <span className="font-medium" style={{ color: '#d97706' }}>{line.split('：')[1]}</span>
                </div>
              return <div key={i} className="text-xs mt-1" style={{ color: '#6b7280' }}>{line}</div>
            })}
          </div>
        )}

        <p className="text-center text-xs mt-6" style={{ color: '#9ca3af' }}>
          🧭 由旅途向导生成 · 仅供参考，请结合实际情况调整
        </p>
      </div>
    </div>
  )
}
