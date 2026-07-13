import { supabase } from './supabase'
import type { MenuItem } from '../types'

// Rows as stored in Supabase `menu_items` — the same table the MCP `get_menu`
// tool reads (mcp-server/server.py). The frontend reads it directly with the
// anon key rather than going through MCP (which holds the service-role key).
interface MenuItemRow {
  id: string
  name: string
  description: string | null
  price: number
  category: string | null
  image_url: string | null
  is_available: boolean
}

const CATEGORIES: MenuItem['category'][] = ['coffee', 'food', 'desserts', 'drinks']

function toMenuItem(row: MenuItemRow): MenuItem {
  const category = (row.category ?? '').toLowerCase() as MenuItem['category']
  return {
    id: row.id,
    name: row.name,
    description: row.description ?? '',
    price: row.price,
    category: CATEGORIES.includes(category) ? category : 'food',
    photo: row.image_url ?? '',
    available: row.is_available,
  }
}

/**
 * Fetch the live menu from Supabase. Mirrors the MCP `get_menu` query
 * (single demo restaurant — no restaurant scoping yet). Returns `null` on
 * failure so callers can fall back to bundled mock data.
 */
export async function fetchMenu(): Promise<MenuItem[] | null> {
  const { data, error } = await supabase
    .from('menu_items')
    .select('id, name, description, price, category, image_url, is_available')
    .eq('is_available', true)
    .order('category')

  if (error || !data) {
    console.error('Failed to fetch menu from Supabase:', error)
    return null
  }

  return (data as MenuItemRow[]).map(toMenuItem)
}
