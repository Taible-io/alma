'use client'

import { useState, useEffect } from 'react'
import type { AppMode, GuestView, CartItem, MenuItem, Order, OrderStatus } from './types'
import { RESTAURANT_NAME, TABLE_NUMBER, MENU_ITEMS, INITIAL_ORDERS } from './data/mockData'
import { supabase } from './lib/supabase'
import { fetchMenu } from './lib/menu'

import WelcomeScreen from './components/guest/WelcomeScreen'
import VoiceScreen from './components/guest/VoiceScreen'
import MenuScreen from './components/guest/MenuScreen'
import OrderReviewSheet from './components/guest/OrderReviewSheet'
import SuccessScreen from './components/guest/SuccessScreen'
import StaffDashboard from './components/staff/StaffDashboard'
import Toast from './components/shared/Toast'

export default function App() {
  const [mode, setMode] = useState<AppMode>('guest')
  const [guestView, setGuestView] = useState<GuestView>('welcome')
  const [cart, setCart] = useState<CartItem[]>([])
  const [cartOpen, setCartOpen] = useState(false)
  const [orders, setOrders] = useState<Order[]>(INITIAL_ORDERS)
  const [menuItems, setMenuItems] = useState(MENU_ITEMS)
  const [toast, setToast] = useState<{ msg: string; type?: 'success' | 'info' | 'error' } | null>(null)
  const [confirmedOrder, setConfirmedOrder] = useState<{ id: string; time: Date; total: number; items: CartItem[] } | null>(null)

  // Load the live menu from Supabase (the same rows MCP's get_menu exposes).
  // Falls back to the bundled mock data if the fetch fails.
  useEffect(() => {
    let active = true
    fetchMenu().then(items => {
      if (active && items && items.length > 0) setMenuItems(items)
    })
    return () => { active = false }
  }, [])

  // Fetch initial orders and subscribe to real-time changes when in staff mode
  useEffect(() => {
    if (mode !== 'staff') return;

    // Load existing orders (mocking transformation for now)
    const fetchOrders = async () => {
      const { data, error } = await supabase
        .from('orders')
        .select('*')
        .order('created_at', { ascending: false });
      
      if (data && !error) {
        // Here you would normally map DB schema to frontend types
        // This is a placeholder showing the integration point
        console.log("Fetched Supabase orders:", data);
      }
    };

    fetchOrders();

    // Subscribe to new orders
    const subscription = supabase
      .channel('public:orders')
      .on('postgres_changes', { event: '*', schema: 'public', table: 'orders' }, payload => {
        console.log('Order change received!', payload);
        if (payload.eventType === 'INSERT') {
          // Play a notification sound or show toast
          setToast({ msg: 'New order received!', type: 'info' });
          // In real app, append to orders state
        } else if (payload.eventType === 'UPDATE') {
          // In real app, update order status in state
        }
      })
      .subscribe();

    return () => {
      supabase.removeChannel(subscription);
    };
  }, [mode]);

  function addToCart(item: MenuItem, qty: number) {
    setCart(prev => {
      const existing = prev.find(c => c.menuItem.id === item.id)
      if (existing) {
        return prev.map(c =>
          c.menuItem.id === item.id ? { ...c, quantity: c.quantity + qty } : c
        )
      }
      return [...prev, { menuItem: item, quantity: qty }]
    })
    setToast({ msg: `${item.name} added`, type: 'success' })
  }

  function removeFromCart(itemId: string) {
    setCart(prev => prev.filter(c => c.menuItem.id !== itemId))
  }

  function confirmOrder(stayOnScreen: boolean = false) {
    const total = cart.reduce((s, i) => s + i.menuItem.price * i.quantity, 0)
    const now = new Date()
    const newOrder: Order = {
      id: `ord-${Date.now()}`,
      tableNumber: TABLE_NUMBER,
      items: cart,
      status: 'new',
      receivedAt: now,
      total,
      isNew: true,
    }
    setConfirmedOrder({ id: newOrder.id, time: now, total, items: [...cart] })
    setOrders(prev => [newOrder, ...prev])
    setCart([])
    setCartOpen(false)
    if (!stayOnScreen) {
      setGuestView('success')
    }
    setTimeout(() => {
      setOrders(prev => prev.map(o => o.id === newOrder.id ? { ...o, isNew: false } : o))
    }, 7000)
  }

  function handleOrderAction(id: string, next: OrderStatus) {
    setOrders(prev => prev.map(o => o.id === id ? { ...o, status: next, isNew: false } : o))
    
    // In a real app, send update to Supabase
    if (mode === 'staff') {
      supabase.from('orders').update({ status: next }).eq('id', id).then(() => {
         console.log(`Order ${id} status updated to ${next} in DB`)
      })
    }
  }

  function handleUpdateMenuItem(item: MenuItem) {
    setMenuItems(prev => prev.map(i => i.id === item.id ? item : i))
  }

  function handleCallStaff() {
    setToast({ msg: 'Staff has been notified', type: 'info' })
  }

  return (
    <>
      {toast && (
        <Toast
          message={toast.msg}
          type={toast.type}
          onDismiss={() => setToast(null)}
        />
      )}

      {mode === 'staff' ? (
        <StaffDashboard
          orders={orders}
          menuItems={menuItems}
          onAction={handleOrderAction}
          onUpdateItem={handleUpdateMenuItem}
          onSwitchToGuest={() => setMode('guest')}
        />
      ) : (
        <>
          {guestView === 'welcome' && (
            <div className="relative">
              <WelcomeScreen
                restaurantName={RESTAURANT_NAME}
                tableNumber={TABLE_NUMBER}
                onStart={() => setGuestView('voice')}
                onBrowseMenu={() => setGuestView('menu')}
              />
            </div>
          )}

          {guestView === 'voice' && (
            <>
              <VoiceScreen
                restaurantName={RESTAURANT_NAME}
                tableNumber={TABLE_NUMBER}
                cart={cart}
                onOpenCart={() => setCartOpen(true)}
                onOpenMenu={() => setGuestView('menu')}
                onCallStaff={handleCallStaff}
                onConfirmOrder={confirmOrder}
                onSetCart={(items) => setCart(items)}
              />
              <OrderReviewSheet
                open={cartOpen}
                cart={cart}
                onConfirm={confirmOrder}
                onClose={() => setCartOpen(false)}
                onKeepTalking={() => setCartOpen(false)}
                onRemoveItem={removeFromCart}
              />
            </>
          )}

          {guestView === 'menu' && (
            <>
              <MenuScreen
                items={menuItems}
                cart={cart}
                onAddToCart={addToCart}
                onOpenCart={() => setCartOpen(true)}
                onBack={() => setGuestView(cart.length > 0 ? 'voice' : 'welcome')}
              />
              <OrderReviewSheet
                open={cartOpen}
                cart={cart}
                onConfirm={confirmOrder}
                onClose={() => setCartOpen(false)}
                onKeepTalking={() => setCartOpen(false)}
                onRemoveItem={removeFromCart}
              />
            </>
          )}

          {guestView === 'success' && confirmedOrder && (
            <div className="relative">
              <SuccessScreen
                restaurantName={RESTAURANT_NAME}
                tableNumber={TABLE_NUMBER}
                cart={confirmedOrder.items}
                orderId={confirmedOrder.id}
                orderTime={confirmedOrder.time}
                total={confirmedOrder.total}
                onNewOrder={() => { setGuestView('voice'); setConfirmedOrder(null) }}
              />
            </div>
          )}
        </>
      )}
    </>
  )
}

