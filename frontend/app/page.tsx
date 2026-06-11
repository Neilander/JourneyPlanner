'use client'

import { useEffect, useRef, useState, useMemo } from 'react'
let AMapLoader: any = null

const AMAP_KEY = 'f65273afda7993a2685b0337410b8777'
const AMAP_SECRET = '028c0157e51b8eacd67d72d15161b634'
const API_BASE = 'https://api.neiland.xyz'

const XIAN_CENTER: [number, number] = [108.9398, 34.3416]

const XIAN_ATTRACTIONS = [
  { id: '1', name: '大唐不夜城', lng: 108.9605, lat: 34.2227 },
  { id: '2', name: '钟楼',       lng: 108.9408, lat: 34.2582 },
  { id: '3', name: '兵马俑',     lng: 109.2785, lat: 34.3843 },
  { id: '4', name: '城墙',       lng: 108.9408, lat: 34.2600 },
  { id: '5', name: '陕西历史博物馆', lng: 108.9417, lat: 34.2318 },
  { id: '6', name: '回民街',     lng: 108.9340, lat: 34.2660 },
]

interface Attraction {
  id: string
  name: string
  lng: number
  lat: number
}

interface Warning { issue: string; severity: string; frequency: string; detail: string }
interface HotelAnalysis {
  amap_rating: number | null
  amap_reviews: number
  summary: { highlights: string[]; warnings: Warning[]; verdict: string } | null
}
interface Hotel {
  id: string
  name: string
  address: string
  lng: number
  lat: number
  analysis?: HotelAnalysis
}

// Haversine distance in km
function distanceKm(lat1: number, lng1: number, lat2: number, lng2: number) {
  const R = 6371
  const dLat = (lat2 - lat1) * Math.PI / 180
  const dLng = (lng2 - lng1) * Math.PI / 180
  const a = Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) * Math.sin(dLng / 2) ** 2
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a))
}

// Estimate walking minutes from km (avg 5 km/h)
function toMinutes(km: number) {
  return Math.round(km / 5 * 60)
}

export default function Home() {
  const mapRef = useRef<any>(null)
  const AMapRef = useRef<any>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const hotelMarkersRef = useRef<any[]>([])
  const uidRef = useRef<string>('')

  const [tab, setTab] = useState<'map' | 'rank'>('map')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [hotels, setHotels] = useState<Hotel[]>([])
  const [attractions, setAttractions] = useState<Attraction[]>(XIAN_ATTRACTIONS)
  const [cityName, setCityName] = useState('西安')
  const [mapCenter, setMapCenter] = useState<[number, number]>(XIAN_CENTER)
  const [mapReady, setMapReady] = useState(0)  // bumped when map is recreated
  const [searchKeyword, setSearchKeyword] = useState('')
  const [searchResults, setSearchResults] = useState<Hotel[]>([])
  const [searching, setSearching] = useState(false)
  const [showSearch, setShowSearch] = useState(false)
  const [showCityPicker, setShowCityPicker] = useState(false)
  const [showHotelManager, setShowHotelManager] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [cityInput, setCityInput] = useState('')
  const [commuteMode, setCommuteMode] = useState<'transit' | 'driving' | 'walking'>('transit')
  const [commuteMatrix, setCommuteMatrix] = useState<Record<string, Record<string, number>>>({})
  const [matrixLoading, setMatrixLoading] = useState(false)

  // 从URL ?uid= 加载Bot收录的酒店、城市、已选景点（返回是否有待分析的酒店）
  const loadHotels = async (uid: string, firstLoad = false): Promise<boolean> => {
    try {
      const data = await fetch(`${API_BASE}/api/user/hotels?wecom_id=${encodeURIComponent(uid)}`).then(r => r.json())
      const loaded: Hotel[] = (data.hotels || [])
        .filter((h: any) => h.lat && h.lng)
        .map((h: any) => ({
          id: String(h.id), name: h.name, address: '', lng: h.lng, lat: h.lat,
          analysis: h.analysis ?? undefined,
        }))
      if (loaded.length > 0) setHotels(loaded)

      if (firstLoad) {
        // 恢复已选景点（按名称匹配）
        const savedNames: string[] = data.selected_attractions || []
        const city = data.city || '西安'
        setCityName(city)
        if (city !== '西安') {
          const info = await fetch(`${API_BASE}/api/city/info?city=${encodeURIComponent(city)}`).then(r => r.json())
          if (info.center) setMapCenter([info.center.lng, info.center.lat])
          if (info.attractions?.length > 0) {
            setAttractions(info.attractions)
            const ids = new Set<string>(
              info.attractions.filter((a: Attraction) => savedNames.includes(a.name)).map((a: Attraction) => a.id)
            )
            if (ids.size > 0) setSelected(ids)
          }
        } else if (savedNames.length > 0) {
          const ids = new Set<string>(
            XIAN_ATTRACTIONS.filter(a => savedNames.includes(a.name)).map(a => a.id)
          )
          if (ids.size > 0) setSelected(ids)
        }
      }

      // 返回：是否还有酒店没有分析结果（rating和summary都空）
      const hasPending = loaded.some(h => !h.analysis || (h.analysis.amap_rating == null && !h.analysis.summary))
      return hasPending
    } catch { return false }
  }

  useEffect(() => {
    const uid = new URLSearchParams(window.location.search).get('uid')
    if (!uid) return
    uidRef.current = uid

    loadHotels(uid, true).then(hasPending => {
      if (!hasPending) return
      // 有酒店还在分析中，每30秒轮询一次直到全部完成
      const timer = setInterval(async () => {
        const stillPending = await loadHotels(uid)
        if (!stillPending) clearInterval(timer)
      }, 30000)
      // 最多轮询10次（5分钟）
      setTimeout(() => clearInterval(timer), 300000)
    })
  }, [])

  // 地图初始化
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      ;(window as any)._AMapSecurityConfig = { securityJsCode: AMAP_SECRET }
      if (!AMapLoader) AMapLoader = (await import('@amap/amap-jsapi-loader')).default
      AMapLoader.load({ key: AMAP_KEY, version: '2.0' }).then((AMap: any) => {
        if (cancelled) return
        AMapRef.current = AMap
        const map = new AMap.Map(containerRef.current, {
          center: mapCenter,
          zoom: 13,
          mapStyle: 'amap://styles/b61be196980fb631b8ab0740ad73682a',
        })
        mapRef.current = map
        setMapReady(n => n + 1)

        attractions.forEach(a => {
          const marker = new AMap.Marker({
            position: [a.lng, a.lat],
            title: a.name,
            content: `<div style="display:flex;flex-direction:column;align-items:center;cursor:pointer">
              <div style="width:26px;height:26px;background:#4CAF7D;border-radius:50% 50% 50% 0;transform:rotate(-45deg);box-shadow:0 2px 6px rgba(76,175,125,0.45);display:flex;align-items:center;justify-content:center">
                <div style="width:10px;height:10px;background:white;border-radius:50%;transform:rotate(45deg)"></div>
              </div>
              <div style="background:white;color:#2d6a4a;padding:2px 7px;border-radius:8px;font-size:11px;font-weight:700;margin-top:3px;box-shadow:0 1px 5px rgba(0,0,0,0.15);white-space:nowrap">${a.name}</div>
            </div>`,
            offset: new AMap.Pixel(-13, -34),
          })
          marker.setMap(map)
        })
      })
    })()
    return () => { cancelled = true; mapRef.current?.destroy() }
  }, [mapCenter, attractions])

  // Bot加载的酒店上图（等地图ready）
  useEffect(() => {
    if (hotels.length === 0) return
    const tryRender = (retry = 0) => {
      const AMap = AMapRef.current
      const map = mapRef.current
      if (!AMap || !map) {
        if (retry < 20) setTimeout(() => tryRender(retry + 1), 300)
        return
      }
      hotelMarkersRef.current.forEach(m => m.setMap(null))
      hotelMarkersRef.current = []
      hotels.forEach(hotel => {
        const marker = new AMap.Marker({
          position: [hotel.lng, hotel.lat],
          title: hotel.name,
          content: `<div style="display:flex;flex-direction:column;align-items:center;cursor:pointer">
            <div style="width:30px;height:30px;background:#E8524A;border-radius:50% 50% 50% 0;transform:rotate(-45deg);box-shadow:0 2px 8px rgba(232,82,74,0.45);display:flex;align-items:center;justify-content:center">
              <div style="width:12px;height:12px;background:white;border-radius:50%;transform:rotate(45deg)"></div>
            </div>
            <div style="background:white;color:#b03a35;padding:2px 8px;border-radius:8px;font-size:11px;font-weight:700;margin-top:3px;box-shadow:0 1px 5px rgba(0,0,0,0.15);white-space:nowrap;max-width:90px;overflow:hidden;text-overflow:ellipsis">${hotel.name}</div>
          </div>`,
          offset: new AMap.Pixel(-15, -38),
        })
        marker.setMap(map)
        hotelMarkersRef.current.push(marker)
      })
    }
    tryRender()
  }, [hotels, mapReady])

  const toggleSelect = (id: string) => {
    setSelected(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      // 保存已选景点到后端
      if (uidRef.current) {
        const names = attractions.filter(a => next.has(a.id)).map(a => a.name)
        fetch(`${API_BASE}/api/user/selections`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ wecom_id: uidRef.current, attractions: names }),
        }).catch(() => {})
      }
      return next
    })
  }

  const removeHotel = async (hotelId: string) => {
    // 先更新本地（markers useEffect 会自动重绘）
    setHotels(prev => prev.filter(h => h.id !== hotelId))
    // 再同步后端
    if (uidRef.current) {
      fetch(`${API_BASE}/api/user/hotel/${hotelId}?wecom_id=${encodeURIComponent(uidRef.current)}`, {
        method: 'DELETE',
      }).catch(() => {})
    }
  }

  const loadAnalysis = async (hotelList: Hotel[]) => {
    const updated = await Promise.all(hotelList.map(async h => {
      try {
        const data = await fetch(`${API_BASE}/api/hotel/analysis?hotel_id=${h.id}`).then(r => r.json())
        if (data.status === 'done') return { ...h, analysis: data as HotelAnalysis }
      } catch {}
      return h
    }))
    setHotels(updated)
  }

  const fetchCommuteMatrix = async (
    hs: Hotel[], ats: Attraction[], mode: string, city: string
  ) => {
    const selectedAts = ats.filter(a => selected.has(a.id))
    if (hs.length === 0 || selectedAts.length === 0) return
    setMatrixLoading(true)
    try {
      const res = await fetch(`${API_BASE}/api/commute/matrix`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ hotels: hs, attractions: selectedAts, mode, city }),
      }).then(r => r.json())
      setCommuteMatrix(res.matrix || {})
    } catch {}
    setMatrixLoading(false)
  }

  const handleSearch = async () => {
    if (!searchKeyword.trim()) return
    setSearching(true)
    try {
      const res = await fetch(`${API_BASE}/api/poi/search?keyword=${encodeURIComponent(searchKeyword)}&city=${encodeURIComponent(cityName)}`)
      const data = await res.json()
      setSearchResults(data.pois || [])
    } catch {
      setSearchResults([])
    } finally {
      setSearching(false)
    }
  }

  const addHotel = async (hotel: Hotel) => {
    if (hotels.find(h => h.id === hotel.id)) return
    setShowSearch(false)
    setSearchResults([])
    setSearchKeyword('')

    let finalId = hotel.id
    // 同步到后端（有 uid 时）
    if (uidRef.current) {
      try {
        const res = await fetch(`${API_BASE}/api/user/hotel`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            wecom_id: uidRef.current,
            hotel: { name: hotel.name, lat: hotel.lat, lng: hotel.lng, amap_id: hotel.id, city: cityName },
          }),
        }).then(r => r.json())
        if (res.id) finalId = String(res.id)
      } catch {}
    }

    // 用后端返回的真实 DB id 更新本地
    setHotels(prev => [...prev, { ...hotel, id: finalId }])
    mapRef.current?.setCenter([hotel.lng, hotel.lat])
  }

  // Compute ranking based on selected attractions
  const ranking = useMemo(() => {
    if (hotels.length === 0 || selected.size === 0) return []
    const targets = attractions.filter(a => selected.has(a.id))
    const hasMatrix = Object.keys(commuteMatrix).length > 0
    return hotels
      .map(h => {
        const times = targets.map(a => {
          if (hasMatrix && commuteMatrix[h.id]?.[a.id] != null)
            return commuteMatrix[h.id][a.id]
          return toMinutes(distanceKm(h.lat, h.lng, a.lat, a.lng))
        })
        const avg = Math.round(times.reduce((s, t) => s + t, 0) / times.length)
        return { hotel: h, avg, times, targets }
      })
      .sort((a, b) => a.avg - b.avg)
  }, [hotels, selected, commuteMatrix])

  const selectedAttractions = attractions.filter(a => selected.has(a.id))

  // 选中景点或通勤模式变化时重新拉取真实路线
  useEffect(() => {
    if (selected.size > 0 && hotels.length > 0) {
      fetchCommuteMatrix(hotels, attractions, commuteMode, cityName)
    }
  }, [selected, commuteMode, hotels])

  const CITIES = [
    '北京','上海','广州','深圳','成都','杭州','西安','重庆','南京','武汉',
    '苏州','天津','长沙','青岛','厦门','大理','丽江','三亚','桂林','乌鲁木齐',
    '拉萨','昆明','贵阳','哈尔滨','沈阳','济南','郑州','合肥','福州','南昌',
  ]

  const filteredCities = cityInput
    ? CITIES.filter(c => c.includes(cityInput))
    : CITIES

  const switchCity = async (city: string) => {
    if (!city.trim()) return
    setCityName(city)
    setSelected(new Set())
    setShowCityPicker(false)
    setCityInput('')
    try {
      const info = await fetch(`${API_BASE}/api/city/info?city=${encodeURIComponent(city)}`).then(r => r.json())
      if (info.center) setMapCenter([info.center.lng, info.center.lat])
      if (info.attractions?.length > 0) setAttractions(info.attractions)
    } catch {}
  }

  return (
    <div className="flex flex-col h-screen" style={{ background: 'var(--base)' }}>
      {/* City picker modal */}
      {showCityPicker && (
        <div className="absolute inset-0 bg-black/50 z-50 flex flex-col">
          <div className="m-4 mt-16 rounded-2xl overflow-hidden flex flex-col max-h-[70vh]" style={{ background: 'var(--surface)' }}>
            <div className="flex items-center gap-2 p-3 border-b" style={{ borderColor: 'var(--cream)' }}>
              <input
                autoFocus
                className="flex-1 text-sm outline-none bg-transparent"
                style={{ color: 'var(--ink)' }}
                placeholder="搜索城市，如：成都"
                value={cityInput}
                onChange={e => setCityInput(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter' && cityInput.trim()) switchCity(cityInput) }}
              />
              <button onClick={() => setShowCityPicker(false)} className="text-sm" style={{ color: 'var(--ink-mid)' }}>取消</button>
            </div>
            <div className="overflow-y-auto">
              <div className="flex flex-wrap gap-2 p-3">
                {filteredCities.map(c => (
                  <button
                    key={c}
                    onClick={() => switchCity(c)}
                    className="px-3 py-1.5 rounded-full text-sm border transition-colors"
                    style={c === cityName
                      ? { background: 'var(--accent)', color: '#fff', borderColor: 'var(--accent)' }
                      : { borderColor: 'var(--cream)', color: 'var(--ink-mid)' }}
                  >{c}</button>
                ))}
                {filteredCities.length === 0 && cityInput && (
                  <button
                    onClick={() => switchCity(cityInput)}
                    className="px-3 py-1.5 rounded-full text-sm text-white"
                    style={{ background: 'var(--accent)' }}
                  >搜索「{cityInput}」</button>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3" style={{ background: 'var(--surface)', borderBottom: '1px solid var(--cream)' }}>
        <button
          onClick={() => setShowCityPicker(true)}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-full transition-colors"
          style={{ background: 'var(--cream)', color: 'var(--ink)' }}
        >
          <span className="text-sm">📍</span>
          <span className="font-bold text-sm">{cityName}</span>
          <span className="text-xs" style={{ color: 'var(--ink-mid)' }}>切换 ▾</span>
        </button>
        <div className="flex items-center gap-2">
          <div className="flex rounded-full p-0.5 text-sm" style={{ background: 'var(--cream)' }}>
            <button
              onClick={() => setTab('map')}
              className="px-3 py-1 rounded-full transition-colors"
              style={tab === 'map' ? { background: 'var(--primary)', color: '#fff', fontWeight: 600 } : { color: 'var(--ink-mid)' }}
            >地图</button>
            <button
              onClick={() => setTab('rank')}
              className="px-3 py-1 rounded-full transition-colors"
              style={tab === 'rank' ? { background: 'var(--primary)', color: '#fff', fontWeight: 600 } : { color: 'var(--ink-mid)' }}
            >排行榜</button>
          </div>
          <button
            onClick={() => setShowSettings(true)}
            className="text-sm px-3 py-1 rounded-full"
            style={{ background: 'var(--cream)', color: 'var(--ink)' }}
            title="通勤偏好"
          >⚙</button>
          {hotels.length > 0 && (
            <button
              onClick={() => setShowHotelManager(true)}
              className="text-sm px-3 py-1 rounded-full"
              style={hotels.some(h => h.analysis?.summary?.warnings.some(w => w.severity === '高'))
                ? { background: '#e05a4a', color: '#fff' }
                : { background: 'var(--cream)', color: 'var(--ink)' }}
            >
              {hotels.some(h => h.analysis?.summary?.warnings.some(w => w.severity === '高')) ? '⚠ ' : ''}
              酒店 {hotels.length}
            </button>
          )}
          <button
            onClick={() => setShowSearch(true)}
            className="text-sm px-3 py-1 rounded-full text-white"
            style={{ background: 'var(--accent)' }}
          >+ 添加酒店</button>
        </div>
      </div>

      {/* Search modal */}
      {showSearch && (
        <div className="absolute inset-0 bg-black/50 z-50 flex flex-col">
          <div className="m-4 mt-16 rounded-2xl overflow-hidden flex flex-col max-h-[70vh]" style={{ background: 'var(--surface)' }}>
            <div className="flex items-center gap-2 p-3 border-b" style={{ borderColor: 'var(--cream)' }}>
              <input
                autoFocus
                className="flex-1 text-sm outline-none bg-transparent"
                style={{ color: 'var(--ink)' }}
                placeholder="搜索酒店名称，如：西安钟楼亚朵"
                value={searchKeyword}
                onChange={e => setSearchKeyword(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && handleSearch()}
              />
              <button onClick={handleSearch} className="text-sm font-medium" style={{ color: 'var(--accent)' }}>
                {searching ? '搜索中…' : '搜索'}
              </button>
              <button onClick={() => { setShowSearch(false); setSearchResults([]) }} className="text-sm ml-1" style={{ color: 'var(--ink-mid)' }}>
                取消
              </button>
            </div>
            <div className="overflow-y-auto">
              {searchResults.map(p => (
                <div
                  key={p.id}
                  onClick={() => addHotel(p)}
                  className="flex flex-col px-4 py-3 border-b cursor-pointer"
                  style={{ borderColor: 'var(--cream)' }}
                >
                  <span className="text-sm font-medium" style={{ color: 'var(--ink)' }}>{p.name}</span>
                  <span className="text-xs mt-0.5" style={{ color: 'var(--ink-mid)' }}>{p.address}</span>
                </div>
              ))}
              {searchResults.length === 0 && !searching && searchKeyword && (
                <p className="text-center text-sm py-8" style={{ color: 'var(--ink-mid)' }}>没有找到结果</p>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Commute settings panel */}
      {showSettings && (
        <div className="fixed inset-0 bg-black/50 z-[9999] flex flex-col justify-end">
          <div className="rounded-t-3xl p-5" style={{ background: 'var(--surface)' }}>
            <div className="flex items-center justify-between mb-4">
              <span className="font-bold text-base" style={{ color: 'var(--ink)' }}>通勤偏好</span>
              <button onClick={() => setShowSettings(false)} className="text-sm" style={{ color: 'var(--ink-mid)' }}>完成</button>
            </div>
            <div className="space-y-3">
              {([
                { mode: 'transit', icon: '🚇', label: '公共交通优先', desc: '地铁+公交，贴近本地生活' },
                { mode: 'driving', icon: '🚗', label: '最快速度优先', desc: '驾车，不限交通工具' },
                { mode: 'walking', icon: '🚶', label: '步行', desc: '适合景区密集、步行可达场景' },
              ] as const).map(({ mode, icon, label, desc }) => (
                <button
                  key={mode}
                  onClick={() => { setCommuteMode(mode); setCommuteMatrix({}) }}
                  className="w-full flex items-center gap-3 p-3 rounded-2xl border-2 transition-colors"
                  style={commuteMode === mode
                    ? { borderColor: 'var(--accent)', background: '#fdf3e7' }
                    : { borderColor: 'var(--cream)', background: 'transparent' }}
                >
                  <span className="text-2xl">{icon}</span>
                  <div className="text-left">
                    <div className="font-medium text-sm" style={{ color: 'var(--ink)' }}>{label}</div>
                    <div className="text-xs" style={{ color: 'var(--ink-mid)' }}>{desc}</div>
                  </div>
                  {commuteMode === mode && <span className="ml-auto text-lg" style={{ color: 'var(--accent)' }}>✓</span>}
                </button>
              ))}
            </div>
            <p className="text-xs mt-3 text-center" style={{ color: 'var(--ink-mid)' }}>切换后将重新计算真实路线时间</p>
          </div>
        </div>
      )}

      {/* Hotel manager modal */}
      {showHotelManager && (
        <div className="absolute inset-0 bg-black/50 z-50 flex flex-col">
          <div className="m-4 mt-16 rounded-2xl overflow-hidden flex flex-col max-h-[70vh]" style={{ background: 'var(--surface)' }}>
            <div className="flex items-center justify-between p-3 border-b" style={{ borderColor: 'var(--cream)' }}>
              <span className="font-medium text-sm" style={{ color: 'var(--ink)' }}>候选酒店（{hotels.length}家）</span>
              <div className="flex items-center gap-2">
                {uidRef.current && (
                  <button
                    onClick={() => loadHotels(uidRef.current)}
                    className="text-xs px-2 py-0.5 rounded"
                    style={{ color: 'var(--blue)' }}
                    title="刷新分析数据"
                  >↻ 刷新</button>
                )}
                <button onClick={() => setShowHotelManager(false)} className="text-sm" style={{ color: 'var(--ink-mid)' }}>完成</button>
              </div>
            </div>
            <div className="overflow-y-auto">
              {hotels.length === 0 && (
                <p className="text-center text-sm py-8" style={{ color: 'var(--ink-mid)' }}>暂无候选酒店</p>
              )}
              {hotels.map(h => (
                <div key={h.id} className="px-4 py-3 border-b" style={{ borderColor: 'var(--cream)' }}>
                  <div className="flex items-center">
                    <span className="flex-1 text-sm font-medium" style={{ color: 'var(--ink)' }}>{h.name}</span>
                    {h.analysis?.amap_rating && (
                      <span className={`text-xs font-bold mr-2 ${h.analysis.amap_rating >= 4.5 ? 'text-green-600' : h.analysis.amap_rating >= 4.0 ? 'text-orange-500' : 'text-red-500'}`}>
                        ★ {h.analysis.amap_rating}
                      </span>
                    )}
                    <button onClick={() => removeHotel(h.id)} className="text-red-400 text-xs px-2 py-1 rounded">删除</button>
                  </div>
                  {(!h.analysis || (h.analysis.amap_rating == null && !h.analysis.summary)) && (
                    <p className="text-xs mt-1 animate-pulse" style={{ color: 'var(--ink-mid)' }}>⏳ 正在分析中，请稍后刷新…</p>
                  )}
                  {h.analysis?.summary && (
                    <div className="mt-2 space-y-1">
                      {h.analysis.summary.warnings.length > 0 && (
                        <div className="flex flex-wrap gap-1">
                          {h.analysis.summary.warnings.map((w, i) => (
                            <span key={i} title={w.detail} className={`text-xs px-2 py-0.5 rounded-full ${
                              w.severity === '高' ? 'bg-red-100 text-red-600' :
                              w.severity === '中' ? 'bg-orange-100 text-orange-600' :
                              'bg-yellow-100 text-yellow-600'
                            }`}>
                              ⚠ {w.issue}{w.frequency === '个别提到' ? '（少数）' : ''}
                            </span>
                          ))}
                        </div>
                      )}
                      <p className="text-xs italic" style={{ color: 'var(--ink-mid)' }}>{h.analysis.summary.verdict}</p>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Map */}
      <div
        ref={containerRef}
        className="flex-1 w-full"
        style={{ visibility: tab === 'map' ? 'visible' : 'hidden', position: tab === 'map' ? 'relative' : 'absolute', width: '100%', flex: tab === 'map' ? '1' : '0' }}
      />

      {/* Ranking */}
      {tab === 'rank' && (
        <div className="flex-1 overflow-y-auto px-4 py-3" style={{ background: 'var(--base)' }}>
          {selected.size === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-sm" style={{ color: 'var(--ink-mid)' }}>
              <p>选择想去的景点</p>
              <p className="mt-1">查看酒店通勤排行</p>
            </div>
          ) : hotels.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-sm" style={{ color: 'var(--ink-mid)' }}>
              <p>还没有候选酒店</p>
              <button onClick={() => { setTab('map'); setShowSearch(true) }} className="mt-2" style={{ color: 'var(--accent)' }}>+ 添加酒店</button>
            </div>
          ) : (
            <>
              <p className="text-xs mb-3 flex items-center gap-1" style={{ color: 'var(--ink-mid)' }}>
                {matrixLoading
                  ? <span className="animate-pulse" style={{ color: 'var(--accent)' }}>⏳ 正在计算真实路线...</span>
                  : <>
                      {Object.keys(commuteMatrix).length > 0
                        ? { transit: '🚇 公共交通', driving: '🚗 驾车', walking: '🚶 步行' }[commuteMode]
                        : '📐 直线估算（选景点后自动更新）'
                      }
                      · 已选 {selected.size} 个景点
                    </>
                }
              </p>
              {ranking.map((item, i) => (
                <div key={item.hotel.id} className="rounded-2xl p-4 mb-3" style={{ background: 'var(--surface)', boxShadow: '0 2px 8px rgba(74,59,38,0.08)' }}>
                  <div className="flex items-center gap-2 mb-2">
                    <span className="text-sm font-bold w-6 text-center" style={{ color: i === 0 ? '#d4a017' : i === 1 ? '#8a7550' : i === 2 ? 'var(--accent)' : 'var(--ink-mid)' }}>
                      {i + 1}
                    </span>
                    <div className="flex-1 min-w-0">
                      <span className="font-medium text-sm" style={{ color: 'var(--ink)' }}>{item.hotel.name}</span>
                      {item.hotel.analysis?.amap_rating && (
                        <span className={`ml-2 text-xs font-bold ${item.hotel.analysis.amap_rating >= 4.5 ? 'text-green-600' : item.hotel.analysis.amap_rating >= 4.0 ? 'text-yellow-600' : 'text-red-500'}`}>
                          ★{item.hotel.analysis.amap_rating}
                        </span>
                      )}
                      {item.hotel.analysis?.summary?.warnings.filter(w => w.severity === '高').map((w, i) => (
                        <span key={i} title={w.detail} className="ml-1 text-xs px-1.5 rounded bg-red-100 text-red-600">⚠ {w.issue}</span>
                      ))}
                    </div>
                    <span className="font-bold text-sm" style={{ color: 'var(--accent)' }}>均 {item.avg} 分钟</span>
                    <button onClick={() => removeHotel(item.hotel.id)} className="ml-2 text-xs text-red-400">✕</button>
                  </div>
                  <div className="ml-8 space-y-1">
                    {item.targets.map((a, j) => (
                      <div key={a.id} className="flex justify-between text-xs" style={{ color: 'var(--ink-mid)' }}>
                        <span>{a.name}</span>
                        <span>{item.times[j]} 分钟</span>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </>
          )}
        </div>
      )}

      {/* Bottom wave tray — attraction selector */}
      <div className="relative" style={{ zIndex: 10 }}>
        {/* 波浪 SVG */}
        <svg
          viewBox="0 0 390 48"
          preserveAspectRatio="none"
          className="w-full block"
          style={{ height: 48, marginBottom: -1, filter: 'drop-shadow(0 -2px 4px rgba(120,100,60,0.10))' }}
        >
          <path
            d="M0,28 C60,10 110,4 170,18 C210,28 250,36 305,22 C335,14 362,12 390,20 L390,48 L0,48 Z"
            fill="var(--surface)"
          />
        </svg>
        <div className="px-3 pb-4 pt-0" style={{ background: 'var(--surface)' }}>
          <p className="text-xs mb-2" style={{ color: 'var(--ink-mid)' }}>
            {hotels.length > 0 ? `已导入 ${hotels.length} 家酒店 · ` : ''}抽出你想去的景点
          </p>
          <div className="flex gap-2 overflow-x-auto pb-1">
            {attractions.map(a => (
              <button
                key={a.id}
                onClick={() => toggleSelect(a.id)}
                className="flex-shrink-0 px-3 py-2 rounded-full text-sm font-medium transition-colors"
                style={selected.has(a.id)
                  ? { background: 'var(--primary)', color: '#fff' }
                  : { background: 'var(--cream)', color: 'var(--ink-mid)' }}
              >
                {a.name}
              </button>
            ))}
          </div>
          {selected.size > 0 && (
            <p className="text-xs mt-2" style={{ color: 'var(--accent)' }}>已选 {selected.size} 个景点</p>
          )}
        </div>
      </div>
    </div>
  )
}
