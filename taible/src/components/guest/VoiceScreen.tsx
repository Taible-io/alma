"use client"
import { useState, useEffect, useRef, useCallback } from "react"
import { LiveKitRoom, RoomAudioRenderer, useVoiceAssistant } from "@livekit/components-react"
import type { OrbState, Message, CartItem } from "../../types"
import { fetchMenu } from "../../lib/menu"

// Fallback: match ordered items in the assistant's text against the live menu.
// Matches on the full item name or any distinctive (>3 char) word from it, so
// it works for whatever items are actually on the menu — no hardcoded ids.
function matchMenuItems(text: string, menu: CartItem['menuItem'][]): CartItem['menuItem'][] {
  const lower = text.toLowerCase()
  return menu.filter(item => {
    const name = item.name.toLowerCase()
    if (lower.includes(name)) return true
    return name.split(/\s+/).filter(w => w.length > 3).some(w => lower.includes(w))
  })
}

// Custom SVG Icons for a premium look
const Icons = {
  Mic: (props: any) => <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...props}><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>,
  MicOff: (props: any) => <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...props}><line x1="1" y1="1" x2="23" y2="23"/><path d="M9 9v3a3 3 0 0 0 5.12 2.12M15 9.34V4a3 3 0 0 0-5.94-.6"/><path d="M17 16.95A7 7 0 0 1 5 12v-2m14 0v2a7 7 0 0 1-.11 1.23M12 19v4M8 23h8"/></svg>,
  Staff: (props: any) => <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...props}><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/></svg>,
  Menu: (props: any) => <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...props}><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>,
  Cart: (props: any) => <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...props}><circle cx="9" cy="21" r="1"/><circle cx="20" cy="21" r="1"/><path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6"/></svg>,
  Send: (props: any) => <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...props}><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
}

// ── Voice Orb Sub-component ──
function PremiumVoiceOrb({ state, onClick }: { state: OrbState; onClick: () => void }) {
  const isListening = state === "listening" || state === "thinking" || state === "speaking"
  
  // Dynamic styling based on state
  let orbColor = "rgba(212, 168, 98, " // Gold base for premium look
  let innerGlow = ""
  let animationClass = ""
  
  if (state === "idle") {
    orbColor = "rgba(100, 100, 100, "
  } else if (state === "listening") {
    orbColor = "rgba(91, 168, 92, " // Emerald Green
    innerGlow = "0 0 40px rgba(91,168,92,0.6), inset 0 0 20px rgba(91,168,92,0.4)"
    animationClass = "animate-pulse"
  } else if (state === "thinking") {
    orbColor = "rgba(212, 168, 98, " // Gold
    innerGlow = "0 0 30px rgba(212,168,98,0.5), inset 0 0 15px rgba(212,168,98,0.3)"
    animationClass = "animate-spin-slow" // Assume global CSS has this, or use inline
  } else if (state === "speaking") {
    orbColor = "rgba(99, 149, 212, " // Sapphire Blue
    innerGlow = "0 0 50px rgba(99,149,212,0.7), inset 0 0 25px rgba(99,149,212,0.5)"
    animationClass = "animate-bounce"
  } else if (state === "muted") {
    orbColor = "rgba(176, 83, 47, " // Ruby Red
  }

  return (
    <div className="relative flex items-center justify-center cursor-pointer group" onClick={onClick} style={{ width: 180, height: 180 }}>
      {/* Outer ripples */}
      {isListening && (
        <>
          <div className="absolute inset-0 rounded-full animate-ping opacity-20" style={{ backgroundColor: `${orbColor} 1)` }} />
          <div className="absolute inset-4 rounded-full animate-pulse opacity-30" style={{ backgroundColor: `${orbColor} 1)` }} />
        </>
      )}
      
      {/* Main Orb */}
      <div 
        className={`relative z-10 rounded-full flex items-center justify-center overflow-hidden transition-all duration-700 ease-out transform group-hover:scale-105 ${animationClass}`}
        style={{
          width: 120, 
          height: 120,
          background: `linear-gradient(135deg, ${orbColor}0.1) 0%, ${orbColor}0.4) 100%)`,
          backdropFilter: "blur(12px)",
          border: `1px solid ${orbColor}0.5)`,
          boxShadow: innerGlow || `0 8px 32px ${orbColor}0.15)`
        }}
      >
        {/* Glass highlight */}
        <div className="absolute top-0 left-1/4 right-1/4 h-1/3 rounded-full opacity-30" style={{ background: "linear-gradient(180deg, rgba(255,255,255,0.8) 0%, transparent 100%)" }} />
        
        {state === "muted" ? <Icons.MicOff color="#E8DCD0" /> : <Icons.Mic color="#E8DCD0" size={32} />}
      </div>
    </div>
  )
}

function OrbStateSync({ onStateChange }: { onStateChange: (s: OrbState) => void }) {
  const { state } = useVoiceAssistant()
  useEffect(() => {
    const m: Record<string, OrbState> = { connecting:"thinking", initializing:"thinking", listening:"listening", speaking:"speaking" }
    onStateChange(m[state] ?? "idle")
  }, [state, onStateChange])
  return <RoomAudioRenderer />
}

interface VoiceScreenProps {
  restaurantName: string
  tableNumber: number
  cart: CartItem[]
  onOpenCart: () => void
  onOpenMenu: () => void
  onCallStaff: () => void
  onConfirmOrder?: (stayOnScreen?: boolean) => void
  onSetCart?: (items: CartItem[]) => void
}

export default function VoiceScreen({ restaurantName, tableNumber, cart, onOpenCart, onOpenMenu, onCallStaff, onConfirmOrder, onSetCart }: VoiceScreenProps) {
  const [orbState, setOrbState] = useState<OrbState>("idle")
  const [messages, setMessages] = useState<Message[]>([])
  const [inputText, setInputText] = useState("")
  const [isMuted, setIsMuted] = useState(false)
  const [token, setToken] = useState<string|null>(null)
  
  const transcriptRef = useRef<HTMLDivElement>(null)
  const lastTsRef = useRef(0)
  const sessionRef = useRef(false)
  // Live menu (same rows MCP's get_menu reads), loaded from Supabase on mount.
  const menuRef = useRef<CartItem['menuItem'][]>([])

  const cartCount = cart.reduce((s, i) => s + i.quantity, 0)
  const cartTotal = cart.reduce((s, i) => s + i.menuItem.price * i.quantity, 0)
  const sessionActive = messages.length > 0 || sessionRef.current
  const detectedIdsRef = useRef<string[]>([])

  // === ROOT CAUSE FIX ===
  // On mount: wipe stale backend files AND seed lastTsRef to NOW
  // so that ANY messages already in messages.json from a previous session
  // are completely ignored and never trigger the fallback cart parser.
  useEffect(() => {
    const nowTs = Math.floor(Date.now() / 1000)
    lastTsRef.current = nowTs
    detectedIdsRef.current = []
    // Load the live menu from Supabase
    fetchMenu().then(items => { menuRef.current = items ?? [] })
    // Clear backend stale data
    fetch("/api/order", { method: "DELETE" }).catch(() => {})
    fetch("/api/messages", { method: "DELETE" }).catch(() => {})
  }, [])

  // Poll messages.json — also parse AI text for items as fallback
  useEffect(() => {
    const interval = setInterval(async () => {
      try {
        const res = await fetch("/api/messages")
        const data: Array<{id:string,role:string,text:string,ts:number}> = await res.json()
        if (!Array.isArray(data)) return
        const newMsgs = data.filter(m => m.ts > lastTsRef.current)
        if (newMsgs.length === 0) return
        lastTsRef.current = Math.max(...newMsgs.map(m => m.ts))
        setMessages(prev => {
          const ids = new Set(prev.map(m => m.id))
          const toAdd = newMsgs.filter(m => !ids.has(m.id)).map(m => ({ id: m.id, role: m.role as "user"|"assistant", text: m.text, timestamp: new Date(m.ts*1000) }))
          return [...prev, ...toAdd]
        })

        // Fallback: parse AI confirmations like "Sure, I have added that..."
        for (const msg of newMsgs) {
          if (msg.role === 'assistant') {
            const lowerText = msg.text.toLowerCase()
            // ONLY parse if the AI explicitly indicates it added something
            if (lowerText.includes('add') || lowerText.includes('got it')) {
              const detected = matchMenuItems(msg.text, menuRef.current)
              const newItems = detected.filter(it => !detectedIdsRef.current.includes(it.id))
              if (newItems.length > 0) {
                detectedIdsRef.current = [...detectedIdsRef.current, ...newItems.map(it => it.id)]
                // Push them to the backend manually since LLM tool call missed
                for (const menuItem of newItems) {
                  fetch("/api/order", {
                    method: "POST",
                    body: JSON.stringify(menuItem),
                    headers: { "Content-Type": "application/json" }
                  })
                }
              }
            } // NEW CLOSING BRACE
          }
        }
      } catch {}
    }, 800)
    return () => clearInterval(interval)
  }, [])

  // Poll order.json
  useEffect(() => {
    if (!sessionRef.current) return
    const interval = setInterval(async () => {
      try {
        const res = await fetch("/api/order")
        const items: Array<{id:string,name:string,price:number}> = await res.json()
        if (Array.isArray(items)) {
          const grouped: Record<string, CartItem> = {}
          for (const item of items) {
            if (grouped[item.id]) grouped[item.id].quantity++
            else grouped[item.id] = { menuItem: { id: item.id, name: item.name, price: item.price, description: "", category: "food", available: true }, quantity: 1 }
          }
          if (onSetCart) onSetCart(Object.values(grouped))
        }
      } catch {}
    }, 1200)
    return () => clearInterval(interval)
  }, [onSetCart])

  // Auto-scroll
  useEffect(() => {
    if (transcriptRef.current) transcriptRef.current.scrollTop = transcriptRef.current.scrollHeight
  }, [messages])

  async function handleOrbTap() {
    if (orbState === "idle" || orbState === "muted") {
      if (!token) {
        setOrbState("thinking")
        try {
          await fetch("/api/order", {method:"DELETE"})
          lastTsRef.current = 0
          setMessages([])
          sessionRef.current = true
          if (onSetCart) onSetCart([])
          const res = await fetch("/api/livekit/token?room=taible-demo&participantName=Guest")
          const data = await res.json()
          if (data.token) setToken(data.token); else setOrbState("idle")
        } catch { setOrbState("idle") }
      }
    } else {
      setToken(null)
      setOrbState("idle")
      setIsMuted(false)
    }
  }

  function handleMute() {
    setIsMuted(!isMuted)
    if (token) setOrbState(!isMuted ? "muted" : "listening")
  }

  function sendMessage() {
    if (!inputText.trim()) return
    const msg: Message = { id: `u-${Date.now()}`, role: "user", text: inputText, timestamp: new Date() }
    setMessages(m => [...m, msg])
    setInputText("")
  }

  async function handleConfirm() {
    if (onConfirmOrder) onConfirmOrder(false)
    setToken(null)
    setOrbState("idle")
    // ── Clear conversation for next session ──
    setMessages([])
    sessionRef.current = false
    lastTsRef.current = 0
    detectedIdsRef.current = []
    // Clear backend messages too
    fetch("/api/messages", { method: "DELETE" }).catch(()=>{})
    
    const kitchenOrder = {
      id: "ord-" + Date.now(),
      tableNumber,
      items: cart.map(i => ({ id: i.menuItem.id, name: i.menuItem.name, quantity: i.quantity, price: i.menuItem.price })),
      total: cartTotal,
      status: "new",
      receivedAt: new Date().toISOString(),
    }
    fetch("/api/kitchen", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(kitchenOrder) }).catch(()=>{})
  }

  // Define premium status labels
  const STATUS_LABELS = {
    idle: "Ready to assist",
    listening: "Alma is listening",
    thinking: "Alma is thinking",
    speaking: "Alma is speaking",
    muted: "Microphone muted"
  }

  return (
    <div className="min-h-screen flex flex-col font-sans" style={{ background: "radial-gradient(ellipse at top, #1F1B18 0%, #0A0908 100%)", color: "#E8DCD0" }}>
      {token && (
        <LiveKitRoom serverUrl={process.env.NEXT_PUBLIC_LIVEKIT_URL} token={token} connect audio={!isMuted} video={false} onDisconnected={()=>{setToken(null);setOrbState("idle")}}>
          <OrbStateSync onStateChange={setOrbState} />
        </LiveKitRoom>
      )}

      {/* ── Top Header ── */}
      <div className="flex items-center justify-between px-6 py-5 bg-black/20 backdrop-blur-md border-b border-white/5">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl flex items-center justify-center bg-gradient-to-br from-[#D4A862] to-[#8F5E3A] shadow-lg shadow-amber-900/20">
            <svg width="20" height="20" viewBox="0 0 36 36" fill="none">
              <rect x="4" y="10" width="28" height="5" rx="2.5" fill="#FFF"/>
              <rect x="6" y="15" width="4" height="14" rx="2" fill="#FFF"/>
              <rect x="26" y="15" width="4" height="14" rx="2" fill="#FFF"/>
              <circle cx="18" cy="8" r="4" fill="#FFF"/>
            </svg>
          </div>
          <div>
            <h1 className="text-sm font-bold tracking-widest uppercase text-white/90">{restaurantName}</h1>
            <p className="text-xs font-medium text-[#D4A862]">Table {tableNumber}</p>
          </div>
        </div>
        <div className="flex items-center gap-2 px-4 py-1.5 rounded-full bg-white/5 border border-white/10">
          <div className={`w-2 h-2 rounded-full ${orbState === 'listening' ? 'bg-[#5BA85C] animate-pulse' : orbState === 'idle' ? 'bg-white/30' : 'bg-[#D4A862]'}`} />
          <span className="text-xs font-semibold tracking-wide text-white/70">
            {STATUS_LABELS[orbState]}
          </span>
        </div>
      </div>

      {/* ── Main Orb Area ── */}
      <div className="flex flex-col items-center justify-center pt-10 pb-6 flex-shrink-0 relative overflow-hidden">
        {/* Subtle background glow */}
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-64 h-64 bg-[#D4A862] rounded-full blur-[100px] opacity-5 pointer-events-none" />
        
        <PremiumVoiceOrb state={orbState} onClick={handleOrbTap} />
        
        <p className="mt-6 text-sm font-medium tracking-wide text-white/50 transition-all duration-300">
          {orbState === 'idle' ? 'Tap microphone to begin' : 'Alma is with you...'}
        </p>
      </div>

      {/* ── Conversation Transcript ── */}
      <div ref={transcriptRef} className="flex-1 overflow-y-auto px-6 py-4 space-y-6 scroll-smooth" style={{ maxHeight: 'calc(100vh - 460px)' }}>
        {messages.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center opacity-30 gap-4">
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
            <p className="text-sm tracking-wider font-light">Conversation history will appear here</p>
          </div>
        ) : messages.map((msg, idx) => (
          <div key={msg.id} className={`flex w-full ${msg.role === 'user' ? 'justify-end' : 'justify-start'} animate-in fade-in slide-in-from-bottom-2 duration-300`} style={{ animationFillMode: 'both', animationDelay: `${idx * 0.05}s` }}>
            <div className={`max-w-[80%] rounded-2xl px-5 py-3.5 text-sm leading-relaxed shadow-lg ${
              msg.role === 'user' 
                ? 'bg-gradient-to-br from-[#4A3E32] to-[#2A231C] border border-[#5A4E42] text-[#F5F0EB] rounded-br-sm' 
                : 'bg-white/5 backdrop-blur-md border border-white/10 text-white/90 rounded-bl-sm'
            }`}>
              {msg.text}
            </div>
          </div>
        ))}
      </div>

      {/* ── Confirm Button ── */}
      {sessionActive && (
        <div className="px-6 py-3">
          <button 
            onClick={handleConfirm} 
            className="w-full flex items-center justify-center gap-3 py-4 rounded-2xl text-base font-bold text-white transition-all duration-300 active:scale-[0.98] hover:shadow-[0_0_30px_rgba(91,168,92,0.3)] group relative overflow-hidden" 
            style={{ background: "linear-gradient(135deg, #4A9E4B 0%, #2D612E 100%)", border: "1px solid rgba(91,168,92,0.5)" }}
          >
            <div className="absolute inset-0 bg-white/20 translate-y-full group-hover:translate-y-0 transition-transform duration-300 ease-out" />
            <span className="relative z-10 flex items-center gap-2">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
              Confirm Order {cartCount > 0 && <span className="opacity-80 font-medium ml-1">| GBP {cartTotal.toFixed(2)}</span>}
            </span>
          </button>
        </div>
      )}

      {/* ── Bottom Controls ── */}
      <div className="bg-[#141210] border-t border-white/5 pb-6 pt-4 px-6 flex flex-col gap-4 shadow-[0_-10px_40px_rgba(0,0,0,0.3)]">
        
        {/* Text Input */}
        <div className="flex gap-2">
          <div className="flex-1 relative">
            <input 
              type="text" 
              value={inputText} 
              onChange={e => setInputText(e.target.value)} 
              onKeyDown={e => e.key === 'Enter' && sendMessage()} 
              placeholder="Or type your order here..." 
              className="w-full h-12 pl-4 pr-12 rounded-xl text-sm bg-white/5 border border-white/10 text-white placeholder-white/30 focus:outline-none focus:border-[#D4A862]/50 focus:ring-1 focus:ring-[#D4A862]/50 transition-all"
            />
            <button 
              onClick={sendMessage} 
              disabled={!inputText.trim()}
              className="absolute right-2 top-2 bottom-2 w-8 flex items-center justify-center rounded-lg bg-[#D4A862]/20 text-[#D4A862] hover:bg-[#D4A862]/30 transition-colors disabled:opacity-30 disabled:hover:bg-[#D4A862]/20"
            >
              <Icons.Send size={16} />
            </button>
          </div>
        </div>

        {/* Action Bar */}
        <div className="flex items-center justify-between bg-white/5 border border-white/10 p-2 rounded-2xl">
          <button onClick={handleMute} className={`flex-1 flex flex-col items-center gap-1.5 py-2.5 rounded-xl transition-all duration-300 ${isMuted ? 'bg-[#B0532F]/20 text-[#FF855C]' : 'text-white/60 hover:bg-white/5 hover:text-white'}`}>
            {isMuted ? <Icons.MicOff /> : <Icons.Mic />}
            <span className="text-[10px] font-bold uppercase tracking-wider">{isMuted ? 'Muted' : 'Mute'}</span>
          </button>

          <div className="w-[1px] h-8 bg-white/10 mx-1" />

          <button onClick={onCallStaff} className="flex-1 flex flex-col items-center gap-1.5 py-2.5 rounded-xl text-white/60 hover:bg-white/5 hover:text-white transition-all duration-300">
            <Icons.Staff />
            <span className="text-[10px] font-bold uppercase tracking-wider">Staff</span>
          </button>

          <div className="w-[1px] h-8 bg-white/10 mx-1" />

          <button onClick={onOpenMenu} className="flex-1 flex flex-col items-center gap-1.5 py-2.5 rounded-xl text-white/60 hover:bg-white/5 hover:text-white transition-all duration-300">
            <Icons.Menu />
            <span className="text-[10px] font-bold uppercase tracking-wider">Menu</span>
          </button>

          <div className="w-[1px] h-8 bg-white/10 mx-1" />

          <button onClick={onOpenCart} className={`flex-1 flex flex-col items-center gap-1.5 py-2.5 rounded-xl transition-all duration-300 relative ${cartCount > 0 ? 'text-[#D4A862] bg-[#D4A862]/10' : 'text-white/60 hover:bg-white/5 hover:text-white'}`}>
            <div className="relative">
              <Icons.Cart />
              {cartCount > 0 && (
                <div className="absolute -top-2 -right-2 w-4 h-4 bg-[#D4A862] text-black rounded-full flex items-center justify-center text-[10px] font-black">
                  {cartCount}
                </div>
              )}
            </div>
            <span className="text-[10px] font-bold uppercase tracking-wider">Cart</span>
          </button>
        </div>
      </div>
      
    </div>
  )
}
