'use client'

import { useEffect, useRef, useState, useMemo } from 'react'
let AMapLoader: any = null

const AMAP_KEY = 'f65273afda7993a2685b0337410b8777'
const AMAP_SECRET = '028c0157e51b8eacd67d72d15161b634'
const API_BASE = 'https://api.neiland.xyz'

// 地图配色样式 ID（高德个性化地图后台用 public/amap-style.json 生成后，替换这里即可）
const AMAP_STYLE = 'amap://styles/b61be196980fb631b8ab0740ad73682a'

const XIAN_CENTER: [number, number] = [108.9398, 34.3416]

const XIAN_ATTRACTIONS = [
  { id: '1', name: '大唐不夜城', lng: 108.9605, lat: 34.2227 },
  { id: '2', name: '钟楼',       lng: 108.9408, lat: 34.2582 },
  { id: '3', name: '兵马俑',     lng: 109.2785, lat: 34.3843 },
  { id: '4', name: '城墙',       lng: 108.9408, lat: 34.2600 },
  { id: '5', name: '陕西历史博物馆', lng: 108.9417, lat: 34.2318 },
  { id: '6', name: '回民街',     lng: 108.9340, lat: 34.2660 },
]

// 小蜜鼓边按钮形状（从原型烤出的固定路径）
const D_CITY = "M38.2,9.2 C77.7,-0.7 119.7,-0.0 161.4,4.3 C177.4,7.9 183.9,12.6 186.6,24.1 C199.1,42.8 196.5,76.6 186.1,102.3 C184.5,113.0 176.0,118.6 160.0,121.9 C119.4,124.7 81.6,128.0 40.7,119.6 C23.0,115.9 16.3,112.2 14.0,102.6 C0.4,78.1 -1.3,43.3 11.2,21.1 C17.3,14.9 25.3,10.1 38.2,9.2 Z"
const D_SEG = "M42.7,4.2 C78.2,1.9 118.9,0.6 160.9,4.7 C176.2,7.6 184.3,11.5 190.3,17.2 C197.9,32.4 196.3,59.5 188.3,80.3 C184.6,84.7 175.0,89.6 158.6,91.5 C118.1,90.9 81.7,97.0 42.4,89.5 C24.2,87.0 17.3,83.1 12.4,76.0 C0.2,64.0 2.5,33.1 11.6,16.0 C14.7,11.8 24.1,6.7 42.7,4.2 Z"
const D_SET = "M32.4,11.0 C63.7,1.0 91.1,1.8 125.0,13.2 C135.5,13.2 144.3,22.5 145.8,38.1 C155.4,76.4 156.8,123.5 144.5,164.4 C144.2,177.9 137.8,187.3 122.1,186.9 C92.5,201.8 59.7,201.9 32.1,186.1 C17.1,184.6 12.3,176.8 7.7,166.0 C0.3,126.1 -2.0,72.4 6.1,38.4 C11.0,22.2 20.7,14.9 32.4,11.0 Z"
const D_IMP = "M28.7,8.8 C58.2,-0.0 95.1,-0.2 122.4,9.1 C136.1,12.9 145.3,20.4 145.6,33.6 C150.4,72.8 158.5,126.0 144.7,166.3 C142.0,177.1 138.5,186.2 125.2,189.3 C95.6,199.0 64.4,200.7 29.3,192.3 C18.9,185.6 11.1,177.0 10.2,165.7 C1.6,127.6 -1.7,69.8 11.0,38.9 C13.8,23.8 19.6,12.7 28.7,8.8 Z"
const HEART_D = "M12 21s-7.5-4.6-10-9.3C.6 8.9 2 5.5 5.2 5.5c1.9 0 3.2 1.1 3.8 2.3l.5 1 .5-1c.6-1.2 1.9-2.3 3.8-2.3 3.2 0 4.6 3.4 3.2 6.2C19.5 16.4 12 21 12 21z"
const SCENIC = ['🏞️','⛩️','🏯','🌅','🌉','🗼','🏝️','⛰️','🌸','🛕']
// 景点名 → 已下载的图片文件名（public/attractions/*.jpg）；先做了西安一城，匹配不到回退 emoji
const ATTR_IMG: Record<string, string> = {
  '大唐不夜城': 'datang', '钟楼': 'zhonglou', '兵马俑': 'bingmayong',
  '城墙': 'chengqiang', '陕西历史博物馆': 'shaanbo', '回民街': 'huimin',
}
// 比例条：步行段固定绿色；乘车段标签/颜色随当前模式
const WALK_COLOR = '#6fa04a'
const MODE_META: Record<string, { rideLabel: string; rideColor: string; speed: number; walkShare: number }> = {
  transit: { rideLabel: '地铁', rideColor: '#5b86a6', speed: 20, walkShare: 0.25 },
  driving: { rideLabel: '驾车', rideColor: '#e0883c', speed: 30, walkShare: 0 },
  walking: { rideLabel: '',     rideColor: '#6fa04a', speed: 5,  walkShare: 1 },
}

interface Attraction {
  id: string
  name: string
  lng: number
  lat: number
}

// 一段通勤的耗时拆分（后端 /api/commute/matrix 返回）
interface Leg { min: number; walk: number; ride: number }

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
  const cardsRef = useRef<HTMLDivElement>(null)
  const dragRef = useRef({ down: false, sx: 0, sl: 0, moved: false })

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
  // 当前模式下 hotelId -> attrId -> {min, walk, ride}（min 总耗时；walk/ride 用于比例条）
  const [commuteDetail, setCommuteDetail] = useState<Record<string, Record<string, Leg>>>({})
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
          mapStyle: AMAP_STYLE,
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

  // 拉当前模式的通勤矩阵（每格 {min, walk, ride}）
  const fetchCommute = async (hs: Hotel[], ats: Attraction[], mode: string, city: string) => {
    const selectedAts = ats.filter(a => selected.has(a.id))
    if (hs.length === 0 || selectedAts.length === 0) return
    setMatrixLoading(true)
    try {
      const res = await fetch(`${API_BASE}/api/commute/matrix`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ hotels: hs, attractions: selectedAts, mode, city }),
      }).then(r => r.json())
      setCommuteDetail(res.matrix || {})
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

  // Compute ranking — 头条=去各景点平均耗时；比例条=当前模式下 步行 vs 乘车 的时间占比
  const ranking = useMemo(() => {
    if (hotels.length === 0 || selected.size === 0) return []
    const targets = attractions.filter(a => selected.has(a.id))
    const meta = MODE_META[commuteMode] ?? MODE_META.transit
    return hotels
      .map(h => {
        let sumMin = 0, sumWalk = 0, sumRide = 0
        targets.forEach(a => {
          const cell = commuteDetail[h.id]?.[a.id]
          if (cell && typeof cell.min === 'number') {
            sumMin += cell.min; sumWalk += cell.walk; sumRide += cell.ride
          } else {
            // 无真实数据时按直线估算 + 模式步行占比
            const est = Math.round(distanceKm(h.lat, h.lng, a.lat, a.lng) / meta.speed * 60)
            sumMin += est; sumWalk += est * meta.walkShare; sumRide += est * (1 - meta.walkShare)
          }
        })
        const avg = Math.round(sumMin / targets.length)
        const denom = sumWalk + sumRide || 1
        const walkPct = Math.round(sumWalk / denom * 100)
        const bar: { label: string; color: string; pct: number }[] = []
        if (walkPct > 0) bar.push({ label: '步行', color: WALK_COLOR, pct: walkPct })
        if (walkPct < 100 && meta.rideLabel) bar.push({ label: meta.rideLabel, color: meta.rideColor, pct: 100 - walkPct })
        if (bar.length === 0) bar.push({ label: '步行', color: WALK_COLOR, pct: 100 })
        return { hotel: h, avg, bar }
      })
      .sort((a, b) => a.avg - b.avg)
  }, [hotels, selected, commuteDetail, commuteMode])

  const selectedAttractions = attractions.filter(a => selected.has(a.id))
  const hasHighWarning = hotels.some(h => h.analysis?.summary?.warnings.some(w => w.severity === '高'))

  // 选中景点 / 模式 / 酒店变化时，重新拉取当前模式的路线
  useEffect(() => {
    if (selected.size > 0 && hotels.length > 0) {
      fetchCommute(hotels, attractions, commuteMode, cityName)
    }
  }, [selected, commuteMode, hotels])

  // 底部扇形景点卡：弧线 + 拖拽 + 滚轮横滑（命令式，避免每次 selected 变化重挂）
  useEffect(() => {
    const cards = cardsRef.current
    if (!cards) return
    const items = () => Array.from(cards.querySelectorAll('.acard')) as HTMLElement[]

    // 缓存卡片位置，避免 scroll 回调里反复触发 layout
    let posCached: { left: number; w: number }[] = []
    const cachePositions = () => {
      posCached = items().map(c => ({ left: c.offsetLeft, w: c.offsetWidth }))
    }

    const arc = () => {
      const els = items()
      if (window.innerWidth >= 768) {
        for (const el of els) el.style.transform = ''
        return
      }
      const center = cards.scrollLeft + cards.clientWidth / 2
      const span = cards.clientWidth * 0.42
      for (let i = 0; i < els.length; i++) {
        const pos = posCached[i] ?? { left: els[i].offsetLeft, w: els[i].offsetWidth }
        const cc = pos.left + pos.w / 2
        const d = Math.max(-1.4, Math.min(1.4, (cc - center) / span))
        const dd = Math.min(1, d * d)
        const lift = -(1 - dd) * 24
        els[i].style.transform =
          `translateY(${lift.toFixed(1)}px) rotate(${(d * 11).toFixed(2)}deg) scale(${(1 - Math.abs(d) * 0.08).toFixed(3)})`
      }
    }

    let raf = 0
    const onScroll = () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; arc(); updateDots() }) }

    // touch 设备跳过 JS drag，交给浏览器原生 touch 滚动处理（更流畅）
    const ds = dragRef.current
    const onDown = (e: PointerEvent) => {
      if (e.pointerType === 'touch') return
      ds.down = true; ds.moved = false; ds.sx = e.clientX; ds.sl = cards.scrollLeft
    }
    const onMove = (e: PointerEvent) => {
      if (e.pointerType === 'touch' || !ds.down) return
      const dx = e.clientX - ds.sx
      if (!ds.moved && Math.abs(dx) > 4) {
        ds.moved = true
        cards.classList.add('dragging')
        cards.setPointerCapture?.(e.pointerId)
      }
      if (ds.moved) cards.scrollLeft = ds.sl - dx
    }
    const endDrag = (e: PointerEvent) => {
      if (e.pointerType === 'touch' || !ds.down) return
      ds.down = false; cards.classList.remove('dragging')
      try { cards.releasePointerCapture?.(e.pointerId) } catch {}
      const center = cards.scrollLeft + cards.clientWidth / 2
      let best: HTMLElement | null = null, bd = Infinity
      for (const c of items()) {
        const dd = Math.abs(c.offsetLeft + c.offsetWidth / 2 - center)
        if (dd < bd) { bd = dd; best = c }
      }
      if (best) cards.scrollTo({ left: best.offsetLeft + best.offsetWidth / 2 - cards.clientWidth / 2, behavior: 'smooth' })
      setTimeout(() => { ds.moved = false }, 0)
    }
    const onWheel = (e: WheelEvent) => {
      if (Math.abs(e.deltaY) > Math.abs(e.deltaX)) { cards.scrollLeft += e.deltaY; e.preventDefault() }
    }
    const dotsEl = cards.parentElement?.querySelector('.xm-dots') as HTMLElement | null
    const PAGES = Math.min(5, items().length)
    if (dotsEl) { dotsEl.innerHTML = ''; for (let i = 0; i < PAGES; i++) dotsEl.appendChild(document.createElement('i')) }
    const updateDots = () => {
      if (!dotsEl) return
      const max = cards.scrollWidth - cards.clientWidth
      const p = max > 0 ? Math.round(cards.scrollLeft / max * (PAGES - 1)) : 0
      dotsEl.querySelectorAll('i').forEach((d, i) => d.classList.toggle('on', i === p))
    }
    const onResize = () => { cachePositions(); arc() }
    cards.addEventListener('scroll', onScroll, { passive: true })
    cards.addEventListener('pointerdown', onDown)
    cards.addEventListener('pointermove', onMove)
    cards.addEventListener('pointerup', endDrag)
    cards.addEventListener('pointercancel', endDrag)
    cards.addEventListener('wheel', onWheel, { passive: false })
    window.addEventListener('resize', onResize)
    requestAnimationFrame(() => { cachePositions(); arc(); updateDots() })
    return () => {
      cards.removeEventListener('scroll', onScroll)
      cards.removeEventListener('pointerdown', onDown)
      cards.removeEventListener('pointermove', onMove)
      cards.removeEventListener('pointerup', endDrag)
      cards.removeEventListener('pointercancel', endDrag)
      cards.removeEventListener('wheel', onWheel)
      window.removeEventListener('resize', onResize)
    }
  }, [attractions])

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
    <div className="flex flex-col h-screen relative overflow-hidden" style={{ background: 'var(--base)' }}>
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

      {/* Header — 小蜜鼓边顶栏 */}
      <div className="px-3 py-2.5" style={{ background: 'var(--surface)', borderBottom: '1px solid var(--cream)' }}>
        <div className="xm-topbar">
          {/* 城市 */}
          <button className="pbtn b-city" onClick={() => setShowCityPicker(true)}>
            <svg className="shape" viewBox="0 0 200 126" preserveAspectRatio="none"><path fill="var(--gold)" d={D_CITY} /></svg>
            <span className="c"><img src="/icons/icon-pin.png" alt="" /><span>{cityName}</span><span className="chev">▾</span></span>
          </button>

          {/* 地图 / 排行榜 分段 */}
          <button className="pbtn b-seg" aria-label="切换地图与排行榜">
            <svg className="shape" viewBox="0 0 200 95" preserveAspectRatio="none">
              <defs><clipPath id="xm-segclip"><path d={D_SEG} /></clipPath></defs>
              <path fill="var(--cream)" d={D_SEG} />
              <rect x={tab === 'map' ? '0' : '50%'} y="0" width="50%" height="100%" fill="var(--primary)" clipPath="url(#xm-segclip)" style={{ transition: 'x .25s ease' }} />
            </svg>
            <span className={`half l${tab === 'map' ? ' active' : ''}`} onClick={() => setTab('map')}>
              <span className="ic-map" /><span className="t">地图</span>
            </span>
            <span className={`half r${tab === 'rank' ? ' active' : ''}`} onClick={() => setTab('rank')}>
              <span className="ic-bars"><svg width="20" height="18" viewBox="0 0 22 20" fill="currentColor"><rect x="2" y="9" width="5" height="9" rx="1.5" /><rect x="9" y="4" width="5" height="14" rx="1.5" /><rect x="16" y="11" width="5" height="7" rx="1.5" /></svg></span>
              <span className="t">排行榜</span>
            </span>
          </button>

          {/* 设置 */}
          <button className="pbtn b-set" onClick={() => setShowSettings(true)} title="通勤偏好">
            <svg className="shape" viewBox="0 0 156 200" preserveAspectRatio="none"><path fill="var(--cream)" d={D_SET} /></svg>
            <span className="c stack"><img src="/icons/icon-gear.png" alt="" /><span className="t">设置</span></span>
          </button>

          {/* 酒店管理（有酒店时） */}
          {hotels.length > 0 && (
            <button className="pbtn b-set" onClick={() => setShowHotelManager(true)} title="候选酒店">
              <svg className="shape" viewBox="0 0 156 200" preserveAspectRatio="none"><path fill={hasHighWarning ? '#e05a4a' : 'var(--cream)'} d={D_SET} /></svg>
              <span className="c stack">
                <span style={{ fontSize: 19, lineHeight: 1 }}>{hasHighWarning ? '⚠️' : '🏨'}</span>
                <span className="t" style={hasHighWarning ? { color: '#fff' } : undefined}>酒店{hotels.length}</span>
              </span>
            </button>
          )}

          {/* 导入 / 添加酒店 */}
          <button className="pbtn b-imp" onClick={() => setShowSearch(true)} title="添加酒店">
            <svg className="shape" viewBox="0 0 156 200" preserveAspectRatio="none"><path fill="var(--blue)" d={D_IMP} /></svg>
            <span className="c stack"><img src="/icons/icon-download.png" alt="" /><span className="t">导入</span></span>
          </button>
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
                  onClick={() => setCommuteMode(mode)}
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
        <div className="flex-1 overflow-y-auto px-4 py-3" style={{ background: 'var(--base)', paddingBottom: 180 }}>
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
              {/* 标题行 */}
              <div className="flex items-center gap-2 mb-1">
                <span style={{ fontSize: 18 }}>👑</span>
                <h1 className="font-bold" style={{ fontSize: 16, color: 'var(--ink)' }}>酒店通勤时间排行榜</h1>
              </div>
              <p className="text-xs mb-3 flex items-center gap-1 flex-wrap" style={{ color: 'var(--ink-mid)' }}>
                {matrixLoading
                  ? <span className="animate-pulse" style={{ color: 'var(--accent)' }}>⏳ 正在计算真实路线...</span>
                  : <>
                      {Object.keys(commuteDetail).length > 0
                        ? { transit: '🚇 公共交通', driving: '🚗 驾车', walking: '🚶 步行' }[commuteMode]
                        : '📐 直线估算（选景点后自动更新）'
                      }
                      · 已选 {selected.size} 个景点 · 条形=步行/乘车 时间占比
                    </>
                }
              </p>
              {ranking.map((item, i) => {
                const a = item.hotel.analysis
                const rating = a?.amap_rating ?? null
                const rateColor = rating == null ? '' : rating >= 4.5 ? '#4a9a55' : rating >= 4.0 ? '#d08a2c' : '#d04a3c'
                const warnings = a?.summary?.warnings ?? []
                const verdict = a?.summary?.verdict
                const pending = !a || (a.amap_rating == null && !a.summary)
                return (
                  <div key={item.hotel.id} className="xm-rcard">
                    <div className="xm-rtop">
                      <span className="xm-rbadge" style={{
                        background: i === 0 ? 'var(--gold)' : i === 1 ? '#d9c7a0' : i === 2 ? '#e2b48a' : 'var(--cream)',
                        color: i === 0 ? '#7a5a14' : i === 1 ? '#6b5a36' : i === 2 ? '#6e4526' : 'var(--ink-mid)',
                      }}>{i + 1}</span>
                      <span className="xm-rname">{item.hotel.name}</span>
                      <div className="xm-ravg"><div className="lab">平均通勤</div><div className="big">{item.avg}<span>min</span></div></div>
                      <button onClick={() => removeHotel(item.hotel.id)} className="text-xs text-red-400 ml-1">✕</button>
                    </div>
                    <div className="xm-rbar">
                      {item.bar.map((seg, j) => (
                        <span key={j} className="xm-seg" title={`${seg.label} ${seg.pct}%`}
                          style={{ flex: seg.pct || 0.01, background: seg.color }}>
                          {seg.pct >= 15 ? `${seg.label} ${seg.pct}%` : `${seg.pct}%`}
                        </span>
                      ))}
                    </div>
                    <div className="xm-rwarn">
                      {rating != null && <span className="xm-rate" style={{ color: rateColor }}>★ {rating}</span>}
                      {warnings.slice(0, 3).map((w, k) => (
                        <span key={k} title={w.detail} className={`xm-wtag ${w.severity === '高' ? 'hi' : w.severity === '中' ? 'mid' : 'lo'}`}>
                          ⚠ {w.issue}{w.frequency === '个别提到' ? '（少数）' : ''}
                        </span>
                      ))}
                      {pending
                        ? <span className="xm-verdict animate-pulse">⏳ 正在分析避雷信息…</span>
                        : verdict
                          ? <span className={`xm-verdict${warnings.length === 0 ? ' clean' : ''}`}>{warnings.length === 0 ? '✓ ' : ''}{verdict}</span>
                          : (warnings.length === 0 && rating != null
                              ? <span className="xm-verdict clean">✓ 暂无明显雷点</span>
                              : null)}
                    </div>
                  </div>
                )
              })}
            </>
          )}
        </div>
      )}

      {/* Bottom 浮层底板 — 盖在地图上，卡牌可探出 */}
      <div className="xm-dock">
        {/* 底板背景（波浪上沿 + 实底） */}
        <div className="xm-tray-bg">
          <svg viewBox="0 0 390 188" preserveAspectRatio="none">
            <path d="M0,30 C60,12 110,6 170,20 C210,30 250,38 305,24 C335,16 362,14 390,22 L390,188 L0,188 Z" fill="var(--surface)" />
          </svg>
        </div>
        {/* 卡牌（z 在底板之上，可探出） */}
        <div className="xm-dock-inner">
          <div className="xm-cards" ref={cardsRef}>
            {attractions.map((a, i) => (
              <div
                key={a.id}
                className={`acard${selected.has(a.id) ? ' liked' : ''}`}
                onClick={() => { if (!dragRef.current.moved) toggleSelect(a.id) }}
              >
                <span className="heart"><svg viewBox="0 0 24 24"><path d={HEART_D} /></svg></span>
                <div className="pic">
                  {ATTR_IMG[a.name]
                    ? <img src={`/attractions/${ATTR_IMG[a.name]}.jpg`} alt={a.name} />
                    : SCENIC[i % SCENIC.length]}
                </div>
                <div className="ttl"><span className="pin">📍</span>{a.name}</div>
              </div>
            ))}
          </div>
          <div className="xm-dots" />
        </div>
      </div>
    </div>
  )
}
