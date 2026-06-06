'use client'

import { useEffect, useRef, useState } from 'react'
import AMapLoader from '@amap/amap-jsapi-loader'

const AMAP_KEY = 'f65273afda7993a2685b0337410b8777'
const AMAP_SECRET = '028c0157e51b8eacd67d72d15161b634'
const API_BASE = 'http://119.45.235.102'

const XIAN_CENTER: [number, number] = [108.9398, 34.3416]

const ATTRACTIONS = [
  { id: '1', name: '大唐不夜城', lng: 108.9605, lat: 34.2227 },
  { id: '2', name: '钟楼',       lng: 108.9408, lat: 34.2582 },
  { id: '3', name: '兵马俑',     lng: 109.2785, lat: 34.3843 },
  { id: '4', name: '城墙',       lng: 108.9408, lat: 34.2600 },
  { id: '5', name: '陕西历史博物馆', lng: 108.9417, lat: 34.2318 },
  { id: '6', name: '回民街',     lng: 108.9340, lat: 34.2660 },
]

interface Hotel {
  id: string
  name: string
  address: string
  lng: number
  lat: number
}

export default function Home() {
  const mapRef = useRef<any>(null)
  const AMapRef = useRef<any>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const hotelMarkersRef = useRef<any[]>([])

  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [hotels, setHotels] = useState<Hotel[]>([])
  const [searchKeyword, setSearchKeyword] = useState('')
  const [searchResults, setSearchResults] = useState<Hotel[]>([])
  const [searching, setSearching] = useState(false)
  const [showSearch, setShowSearch] = useState(false)

  useEffect(() => {
    ;(window as any)._AMapSecurityConfig = { securityJsCode: AMAP_SECRET }
    AMapLoader.load({ key: AMAP_KEY, version: '2.0' }).then((AMap: any) => {
      AMapRef.current = AMap
      const map = new AMap.Map(containerRef.current, {
        center: XIAN_CENTER,
        zoom: 13,
        mapStyle: 'amap://styles/whitesmoke',
      })
      mapRef.current = map

      ATTRACTIONS.forEach(a => {
        const marker = new AMap.Marker({
          position: [a.lng, a.lat],
          title: a.name,
          label: { content: `<div class="text-xs bg-white px-1 rounded shadow">${a.name}</div>`, direction: 'top' },
        })
        marker.setMap(map)
      })
    })
    return () => mapRef.current?.destroy()
  }, [])

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

  return (
    <div className="flex flex-col h-screen bg-gray-900">
      {/* 顶部 */}
      <div className="flex items-center justify-between px-4 py-3 bg-gray-800 text-white">
        <span className="font-bold text-lg">西安</span>
        <button
          onClick={() => setShowSearch(true)}
          className="text-sm bg-orange-500 px-3 py-1 rounded-full"
        >
          + 添加酒店
        </button>
      </div>

      {/* 搜索弹窗 */}
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

      {/* 地图 */}
      <div ref={containerRef} className="flex-1 w-full" />

      {/* 底部景点选择 */}
      <div className="bg-gray-800 px-3 py-3">
        <p className="text-gray-400 text-xs mb-2">
          {hotels.length > 0 ? `已导入 ${hotels.length} 家酒店 · ` : ''}选择景点查看通勤
        </p>
        <div className="flex gap-2 overflow-x-auto pb-1">
          {ATTRACTIONS.map(a => (
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
