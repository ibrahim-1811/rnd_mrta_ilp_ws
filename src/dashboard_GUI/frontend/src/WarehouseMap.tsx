import { memo, useId, useMemo, useRef, useState } from 'react'
import { Focus, Layers3, Minus, Plus, Move } from 'lucide-react'
import { screenCell } from './model'
import type { Geometry, Snapshot } from './types'

const zoneColor: Record<string, string> = { shelf: '#c5c8ba', staging: '#d4e5bd', dropoff: '#bfd7e4', charging: '#dac9eb', charge_queue: '#e9dff1' }
export const WarehouseMap = memo(function WarehouseMap({ geometry, frame, selected, onSelect, loading }: {
  geometry: Geometry | null; frame: Snapshot | null; selected: string | null; onSelect: (id: string) => void; loading: boolean
}) {
  const [lanes, setLanes] = useState(false)
  const [zoom, setZoom] = useState(1)
  const [pan, setPan] = useState([0, 0])
  const drag = useRef<{ x: number; y: number; pan: number[] } | null>(null)
  const marker = useId().replaceAll(':', '')
  const staticMap = useMemo(() => {
    if (!geometry) return null
    const g = geometry
    return <>
      <rect x="-.5" y="-.5" width={g.width} height={g.height} fill="#fafbf7" stroke="#c8cfc0" strokeWidth=".14" />
      {g.traversable_cells.map(c => { const [x, y] = screenCell(c, g.height); return <rect key={`f${c}`} x={x - .5} y={y - .5} width="1" height="1" fill="none" stroke="#e7eae1" strokeWidth=".035" /> })}
      {g.zones.flatMap(zone => zone.cells.map(c => { const [x, y] = screenCell(c, g.height); return <rect key={`${zone.id}-${c}`} x={x - .46} y={y - .46} width=".92" height=".92" rx=".09" fill={zoneColor[zone.category] ?? '#d2d7ce'}><title>{zone.id} · {zone.category} · [{c.join(', ')}]</title></rect> }))}
      {g.obstacles.map(c => { const [x, y] = screenCell(c, g.height); return <rect key={`w${c}`} x={x - .5} y={y - .5} width="1" height="1" rx=".06" fill="#34413d" /> })}
      {lanes && <g stroke="#7c8c7c" strokeWidth=".07" opacity=".65" markerEnd={`url(#${marker})`}>
        {g.directed_edges.map(([a, b], i) => { const [x1, y1] = screenCell(a, g.height), [x2, y2] = screenCell(b, g.height); return <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} /> })}
      </g>}
    </>
  }, [geometry, lanes, marker])
  return <section className="panel map-panel" aria-label="Warehouse map">
    <div className="panel-heading"><div><span className="eyebrow">WAREHOUSE VIEW</span><h2>{geometry?.id ?? 'Warehouse'} <span className="subtle">/ floor plan</span></h2></div>
      <button className={`button small ${lanes ? 'selected' : 'ghost'}`} aria-pressed={lanes} onClick={() => setLanes(v => !v)}><Layers3 size={14} /> Lanes</button></div>
    <div className="map-stage">
      {!geometry ? <div className="empty"><Layers3 size={30} /><strong>{loading ? 'Loading warehouse geometry…' : 'Warehouse unavailable'}</strong><span>Geometry comes from the existing map loader.</span></div> : <>
        <div className="map-coordinate-label">{geometry.width} × {geometry.height} cells <span>·</span> Directed grid</div>
        <svg className="warehouse-svg" role="img" aria-label={`${geometry.id} warehouse grid, ${geometry.width} by ${geometry.height} cells`}
          viewBox={`-3 -3 ${geometry.width + 5} ${geometry.height + 5}`}
          onPointerDown={e => { if ((e.target as Element).closest('[data-robot]')) return; drag.current = { x: e.clientX, y: e.clientY, pan }; e.currentTarget.setPointerCapture?.(e.pointerId) }}
          onPointerMove={e => { if (!drag.current) return; const box = e.currentTarget.getBoundingClientRect(); const factor = (geometry.width + 5) / Math.max(box.width, 1); setPan([drag.current.pan[0] + (e.clientX - drag.current.x) * factor, drag.current.pan[1] + (e.clientY - drag.current.y) * factor]) }}
          onPointerUp={() => { drag.current = null }} onPointerCancel={() => { drag.current = null }}>
          <defs><marker id={marker} markerWidth="4" markerHeight="4" refX="3" refY="2" orient="auto"><path d="M0 0L4 2L0 4Z" fill="#7c8c7c" /></marker></defs>
          <g transform={`translate(${pan[0]} ${pan[1]}) translate(${geometry.width / 2} ${geometry.height / 2}) scale(${zoom}) translate(${-geometry.width / 2} ${-geometry.height / 2})`}>
            {staticMap}
            {Array.from({ length: Math.ceil(geometry.width / 5) }, (_, n) => n * 5).map(x => <text key={`x${x}`} x={x} y={geometry.height + .9} className="grid-axis" textAnchor="middle">{x}</text>)}
            {Array.from({ length: Math.ceil(geometry.height / 5) }, (_, n) => n * 5).map(y => <text key={`y${y}`} x="-1.35" y={geometry.height - 1 - y + .25} className="grid-axis" textAnchor="middle">{y}</text>)}
            {frame?.payload.robots.map(robot => {
              const [x, y] = screenCell(robot.cell, geometry.height)
              if (robot.cell[0] < 0 || robot.cell[0] >= geometry.width || robot.cell[1] < 0 || robot.cell[1] >= geometry.height) return null
              return <g key={robot.robot_id} data-robot={robot.robot_id} className="robot-marker" role="button" tabIndex={0}
                aria-label={`Select ${robot.robot_id}, cell ${robot.cell.join(', ')}`} onClick={() => onSelect(robot.robot_id)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(robot.robot_id) } }}>
                <title>{robot.robot_id} · {robot.status} · [{robot.cell.join(', ')}]</title>
                <circle cx={x} cy={y} r={selected === robot.robot_id ? 1.05 : .85} fill="#f6f9ef" opacity=".92" />
                <circle cx={x} cy={y} r=".65" fill={robot.charge_state === 'CHARGING' ? '#9270b3' : '#4b7137'} stroke={selected === robot.robot_id ? '#182b12' : '#fff'} strokeWidth=".16" />
                <text x={x} y={y + .18} textAnchor="middle" className="robot-map-label">{robot.robot_id.slice(-3)}</text>
              </g>
            })}
          </g>
        </svg>
        {!frame && <div className="map-observation-note"><span className="dot neutral" /> No fleet observation selected</div>}
        <div className="map-controls"><span><Move size={12} /> Drag to pan</span>
          <button aria-label="Zoom out" title="Zoom out" onClick={() => setZoom(z => Math.max(.6, z / 1.25))}><Minus size={15} /></button>
          <button aria-label="Fit warehouse" title="Fit warehouse" onClick={() => { setZoom(1); setPan([0, 0]) }}><Focus size={15} /></button>
          <button aria-label="Zoom in" title="Zoom in" onClick={() => setZoom(z => Math.min(4, z * 1.25))}><Plus size={15} /></button>
        </div>
      </>}
    </div>
    <div className="map-legend">{Object.entries({ shelf: 'Shelf', staging: 'Staging', dropoff: 'Drop-off', charging: 'Charging', charge_queue: 'Holding' }).map(([key, label]) => <span key={key}><i style={{ background: zoneColor[key] }} />{label}</span>)}<span className="legend-scale">1 cell = {geometry?.resolution_m ?? '—'} m · Y ↑</span></div>
  </section>
})
