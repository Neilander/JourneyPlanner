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

interface Hotel {
  id: string
  name: string
  address: string
  lng: number
  lat: number
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

  const [tab, setTab] = useState<'map' | 'rank'>('map')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [hotels, setHotels] = useState<Hotel[]>([])
  const [attractions, setAttractions] = useState<Attraction[]>(XIAN_ATTRACTIONS)
  const [cityName, setCityName] = useState('西安')
  const [mapCenter, setMapCenter] = useState<[number, number]>(XIAN_CENTER)
  const [searchKeyword, setSearchKeyword] = useState('')
  const [searchResults, setSearchResults] = useState<Hotel[]>([])
  const [searching, setSearching] = useState(false)
  const [showSearch, setShowSearch] = useState(false)

  // 从URL ?uid= 加载Bot收录的酒店和城市
  useEffect(() => {
    const uid = new URLSearchParams(window.location.search).get('uid')
    if (!uid) return
    fetch(`${API_BASE}/api/user/hotels?wecom_id=${encodeURIComponent(uid)}`)
      .then(r => r.json())
      .then(async data => {
        const loaded: Hotel[] = (data.hotels || [])
          .filter((h: any) => h.lat && h.lng)
          .map((h: any) => ({ id: String(h.id), name: h.name, address: '', lng: h.lng, lat: h.lat }))
        if (loaded.length > 0) setHotels(loaded)

        // 加载城市景点和中心
        const city = data.city || '西安'
        if (city !== '西安') {
          setCityName(city)
          const info = await fetch(`${API_BASE}/api/city/info?city=${encodeURIComponent(city)}`).then(r => r.json())
          if (info.center) setMapCenter([info.center.lng, info.center.lat])
          if (info.attractions?.length > 0) setAttractions(info.attractions)
        }
      })
      .catch(() => {})
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
          mapStyle: 'amap://styles/whitesmoke',
        })
        mapRef.current = map

        attractions.forEach(a => {
          const marker = new AMap.Marker({
            position: [a.lng, a.lat],
            title: a.name,
            label: { content: `<div class="text-xs bg-white px-1 rounded shadow">${a.name}</div>`, direction: 'top' },
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
          content: `<div style="display:flex;flex-direction:column;align-items:center">
            <div style="background:#f97316;color:white;padding:3px 8px;border-radius:12px;font-size:12px;font-weight:600;white-space:nowrap;box-shadow:0 2px 6px rgba(0,0,0,0.25)">${hotel.name}</div>
            <div style="width:10px;height:10px;background:#f97316;border-radius:50%;margin-top:3px;box-shadow:0 1px 4px rgba(0,0,0,0.3)"></div>
          </div>`,
          offset: new AMap.Pixel(-40, -28),
        })
        marker.setMap(map)
        hotelMarkersRef.current.push(marker)
      })
    }
    tryRender()
  }, [hotels])

  const toggleSelect = (id: string) => {
    setSelected(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  const handleSearch = async () => {
    if (!searchKeyword.trim()) return
    setSearching(true)
    try {
      const res = await fetch(`${API_BASE}/api/poi/search?keyword=${encodeURIComponent(searchKeyword)}`)
      const data = await res.json()
      setSearchResults(data.pois || [])
    } catch {
      setSearchResults([])
    } finally {
      setSearching(false)
    }
  }

  const addHotel = (hotel: Hotel) => {
    if (hotels.find(h => h.id === hotel.id)) return
    setHotels(prev => [...prev, hotel])
    setShowSearch(false)
    setSearchResults([])
    setSearchKeyword('')

    const AMap = AMapRef.current
    const map = mapRef.current
    if (!AMap || !map) return

    const marker = new AMap.Marker({
      position: [hotel.lng, hotel.lat],
      title: hotel.name,
      content: `<div style="display:flex;flex-direction:column;align-items:center">
        <div style="background:#f97316;color:white;padding:3px 8px;border-radius:12px;font-size:12px;font-weight:600;white-space:nowrap;box-shadow:0 2px 6px rgba(0,0,0,0.25)">${hotel.name}</div>
        <div style="width:10px;height:10px;background:#f97316;border-radius:50%;margin-top:3px;box-shadow:0 1px 4px rgba(0,0,0,0.3)"></div>
      </div>`,
      offset: new AMap.Pixel(-40, -28),
    })
    marker.setMap(map)
    hotelMarkersRef.current.push(marker)
    map.setCenter([hotel.lng, hotel.lat])
  }

  // Compute ranking based on selected attractions
  const ranking = useMemo(() => {
    if (hotels.length === 0 || selected.size === 0) return []
    const targets = attractions.filter(a => selected.has(a.id))
    return hotels
      .map(h => {
        const times = targets.map(a => toMinutes(distanceKm(h.lat, h.lng, a.lat, a.lng)))
        const avg = Math.round(times.reduce((s, t) => s + t, 0) / times.length)
        return { hotel: h, avg, times, targets }
      })
      .sort((a, b) => a.avg - b.avg)
  }, [hotels, selected])

  const selectedAttractions = attractions.filter(a => selected.has(a.id))

  return (
    <div className="flex flex-col h-screen bg-gray-900">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 bg-gray-800 text-white">
        <span className="font-bold text-lg">{cityName}</span>
        <div className="flex items-center gap-2">
          <div className="flex bg-gray-700 rounded-full p-0.5 text-sm">
            <button
              onClick={() => setTab('map')}
              className={`px-3 py-1 rounded-full transition-colors ${tab === 'map' ? 'bg-white text-gray-900 font-medium' : 'text-gray-400'}`}
            >地图</button>
            <button
              onClick={() => setTab('rank')}
              className={`px-3 py-1 rounded-full transition-colors ${tab === 'rank' ? 'bg-white text-gray-900 font-medium' : 'text-gray-400'}`}
            >排行榜</button>
          </div>
          <button
            onClick={() => setShowSearch(true)}
            className="text-sm bg-orange-500 px-3 py-1 rounded-full"
          >+ 添加酒店</button>
        </div>
      </div>

      {/* Search modal */}
      {showSearch && (
        <div className="absolute inset-0 bg-black/60 z-50 flex flex-col">
          <div className="bg-white m-4 mt-16 rounded-xl overflow-hidden flex flex-col max-h-[70vh]">
            <div className="flex items-center gap-2 p-3 border-b">
              <input
                autoFocus
                className="flex-1 text-sm outline-none"
                placeholder="搜索酒店名称，如：西安钟楼亚朵"
                value={searchKeyword}
                onChange={e => setSearchKeyword(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && handleSearch()}
              />
              <button onClick={handleSearch} className="text-orange-500 text-sm font-medium">
                {searching ? '搜索中…' : '搜索'}
              </button>
              <button onClick={() => { setShowSearch(false); setSearchResults([]) }} className="text-gray-400 text-sm ml-1">
                取消
              </button>
            </div>
            <div className="overflow-y-auto">
              {searchResults.map(p => (
                <div
                  key={p.id}
                  onClick={() => addHotel(p)}
                  className="flex flex-col px-4 py-3 border-b cursor-pointer hover:bg-gray-50"
                >
                  <span className="text-sm font-medium text-gray-900">{p.name}</span>
                  <span className="text-xs text-gray-400 mt-0.5">{p.address}</span>
                </div>
              ))}
              {searchResults.length === 0 && !searching && searchKeyword && (
                <p className="text-center text-gray-400 text-sm py-8">没有找到结果</p>
              )}
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
        <div className="flex-1 overflow-y-auto bg-gray-900 px-4 py-3">
          {selected.size === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-gray-500 text-sm">
              <p>选择想去的景点</p>
              <p className="mt-1">查看酒店通勤排行</p>
            </div>
          ) : hotels.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-gray-500 text-sm">
              <p>还没有候选酒店</p>
              <button onClick={() => { setTab('map'); setShowSearch(true) }} className="mt-2 text-orange-400">+ 添加酒店</button>
            </div>
          ) : (
            <>
              <p className="text-gray-400 text-xs mb-3">
                按平均步行距离排序（直线估算）· 已选 {selected.size} 个景点
              </p>
              {ranking.map((item, i) => (
                <div key={item.hotel.id} className="bg-gray-800 rounded-xl p-4 mb-3">
                  <div className="flex items-center gap-2 mb-2">
                    <span className={`text-sm font-bold w-6 text-center ${i === 0 ? 'text-yellow-400' : i === 1 ? 'text-gray-300' : i === 2 ? 'text-orange-400' : 'text-gray-500'}`}>
                      {i + 1}
                    </span>
                    <span className="text-white font-medium text-sm flex-1">{item.hotel.name}</span>
                    <span className="text-orange-400 font-bold text-sm">avg {item.avg} min</span>
                  </div>
                  <div className="ml-8 space-y-1">
                    {item.targets.map((a, j) => (
                      <div key={a.id} className="flex justify-between text-xs text-gray-400">
                        <span>{a.name}</span>
                        <span>{item.times[j]} min</span>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </>
          )}
        </div>
      )}

      {/* Bottom attraction selector */}
      <div className="bg-gray-800 px-3 py-3">
        <p className="text-gray-400 text-xs mb-2">
          {hotels.length > 0 ? `已导入 ${hotels.length} 家酒店 · ` : ''}选择景点查看通勤
        </p>
        <div className="flex gap-2 overflow-x-auto pb-1">
          {attractions.map(a => (
            <button
              key={a.id}
              onClick={() => toggleSelect(a.id)}
              className={`flex-shrink-0 px-3 py-2 rounded-full text-sm font-medium transition-colors ${
                selected.has(a.id)
                  ? 'bg-orange-500 text-white'
                  : 'bg-gray-700 text-gray-300'
              }`}
            >
              {a.name}
            </button>
          ))}
        </div>
        {selected.size > 0 && (
          <p className="text-orange-400 text-xs mt-2">已选 {selected.size} 个景点</p>
        )}
      </div>
    </div>
  )
}
