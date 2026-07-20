/**
 * PlannerMode interaction tests — click spends through the engine,
 * right-click refunds with the no-stranding guard, blocked actions surface
 * their reason, and Save round-trips the allocations.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

import type { AANode, AATreeData } from '../../components/AATree'
import { PlannerMode } from './PlannerMode'
import { downloadAAFile } from './aaFile'
import type { AAConfig, CharAAsResponse } from '../CharacterAAsTab'

// Keep the XML builder real; only the browser download side-effect is mocked.
vi.mock('./aaFile', async importOriginal => ({
  ...(await importOriginal<typeof import('./aaFile')>()),
  downloadAAFile: vi.fn(),
}))

const node = (id: number, name: string, over: Partial<AANode> = {}): AANode => ({
  node_id: id,
  name,
  description: '',
  classification: 'Strength',
  xcoord: 1,
  ycoord: 1,
  icon_id: 1, // >0 so the <img alt={name}> renders (our click target)
  backdrop_id: -1,
  maxtier: 10,
  pointspertier: 1,
  points_to_unlock: 0,
  title: '',
  spellcrc: 0,
  ...over,
})

const TREE: AATreeData = {
  tree_id: 42,
  tree_name: 'Shaman',
  tree_type: 'class',
  nodes: [
    node(1, 'Leg Bite'),
    node(2, 'Aura of Haste'),
    node(3, 'Aura of Warding'),
    node(4, 'Spiritual Foresight', { maxtier: 1, classification_points_required: 22 }),
  ],
}

const CONFIG: AAConfig = {
  xpac: 'Echoes of Faydwer',
  aa_cap: 100,
  tradeskill_aa_cap: 45,
  unlocked_tree_types: ['class'],
}

const charAAs = (spent: Record<string, number>): CharAAsResponse => ({
  character_name: 'Badbang',
  total_spent: Object.values(spent).reduce((a, b) => a + b, 0),
  trees: [{ tree_id: 42, tree_type: 'class', tree_name: 'Shaman', spent, total_spent: 0 }],
  profiles: [],
})

function stubFetch() {
  const calls: { url: string; init?: RequestInit }[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init })
    const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body })
    if (url.includes('/api/aa/config?xpac=')) {
      const xpac = decodeURIComponent(url.split('xpac=')[1])
      return ok({
        xpac,
        aa_cap: xpac === 'Kingdom of Sky' ? 50 : 300,
        tradeskill_aa_cap: 0,
        unlocked_tree_types: ['class'],
        visible_rows: { class: [0, 1, 2, 3, 4] },
      })
    }
    if (url.includes('/api/aa/plans?')) return ok([])
    if (url.includes('/api/aa/plans')) {
      return ok({
        id: 5, name: 'New plan', xpac: 'EoF', share_slug: 'slug123',
        created_at: 1, updated_at: 1, character_name: 'Badbang', world: 'Wuoshi',
        allocations: {}, is_mine: true,
      })
    }
    return ok({})
  }) as unknown as typeof fetch)
  return calls
}

function renderPlanner(spent: Record<string, number> = {}, over: { cls?: string; config?: AAConfig } = {}) {
  return render(
    <PlannerMode
      charName="Badbang"
      cls={over.cls}
      charAAs={charAAs(spent)}
      config={over.config ?? CONFIG}
      treeData={new Map([[42, TREE]])}
    />,
  )
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe('PlannerMode', () => {
  it('click spends a rank; right-click refunds it', async () => {
    stubFetch()
    renderPlanner()
    const legBite = await screen.findByAltText('Leg Bite')
    fireEvent.click(legBite)
    expect(await screen.findByText('(1)')).toBeInTheDocument() // tree tab counter
    fireEvent.contextMenu(legBite)
    expect(await screen.findByText('(0)')).toBeInTheDocument()
  })

  it('blocked spends surface the engine reason', async () => {
    stubFetch()
    renderPlanner()
    fireEvent.click(await screen.findByAltText('Spiritual Foresight'))
    // The reason shows in the status line (and also in the node tooltip).
    const status = await screen.findByRole('status')
    expect(status).toHaveTextContent('Requires 22 points spent in Strength')
    expect(screen.getByText('(0)')).toBeInTheDocument() // nothing spent
  })

  it('hovering a locked node shows the unmet requirement in its tooltip', async () => {
    stubFetch()
    renderPlanner()
    fireEvent.mouseEnter(await screen.findByAltText('Spiritual Foresight'))
    // The reason renders inside the node tooltip (portal), before any click.
    expect(await screen.findByText('Requires 22 points spent in Strength')).toBeInTheDocument()
  })

  it('blocks refunds that would strand a taken prerequisite', async () => {
    stubFetch()
    renderPlanner({ '1': 10, '2': 10, '3': 2, '4': 1 }) // exactly 22 in line + the final
    fireEvent.contextMenu(await screen.findByAltText('Aura of Warding'))
    expect(await screen.findByText(/Spiritual Foresight would lose its requirement/)).toBeInTheDocument()
    expect(screen.getByText('(23)')).toBeInTheDocument() // untouched
  })

  it('resolver adds class trees the character lacks — but only era-unlocked ones', async () => {
    const calls = stubFetch()
    // Resolver knows Templar has a shadows tree (id 90) the char's Census
    // record doesn't carry; tree 90 is fetched on demand.
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init })
      const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body })
      if (url.includes('/api/aa/plan-trees')) {
        return ok([
          { tree_id: 42, tree_type: 'class', tree_name: 'Shaman' },
          { tree_id: 90, tree_type: 'shadows', tree_name: 'Shadows' },
        ])
      }
      if (url.includes('/api/aa/tree/90')) {
        return ok({ tree_id: 90, tree_name: 'Shadows', tree_type: 'shadows', nodes: [node(900, 'Shadow Skill')] })
      }
      if (url.includes('/api/aa/plans?')) return ok([])
      return ok({})
    }) as unknown as typeof fetch)

    const tsoConfig: AAConfig = { ...CONFIG, xpac: 'The Shadow Odyssey', aa_cap: 200, unlocked_tree_types: ['class', 'shadows'] }
    renderPlanner({}, { cls: 'Templar', config: tsoConfig })
    // Both tabs appear: the char's class tree + the resolver's shadows tree.
    expect(await screen.findByRole('button', { name: /Shadows/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Shaman/ })).toBeInTheDocument()
  })

  it('era dropdown re-plans under the selected expansion rules', async () => {
    const calls = stubFetch()
    renderPlanner()
    // Server era first: budget shows the prop config's cap.
    expect(await screen.findByText('/ 100')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Era'), { target: { value: 'Kingdom of Sky' } })
    // Era config fetched with ?xpac= and the budget re-caps to 50.
    expect(await screen.findByText('/ 50')).toBeInTheDocument()
    expect(calls.some(c => c.url.includes('/api/aa/config?xpac=Kingdom%20of%20Sky'))).toBe(true)
    // Back to the server era restores the prop config.
    fireEvent.change(screen.getByLabelText('Era'), { target: { value: '' } })
    expect(await screen.findByText('/ 100')).toBeInTheDocument()
  })

  it('Download .aa exports the plan as an in-game spec file', async () => {
    stubFetch()
    renderPlanner()
    const download = await screen.findByRole('button', { name: /Download \.aa/ })
    expect(download).toBeDisabled() // empty plan — nothing to export
    fireEvent.click(await screen.findByAltText('Leg Bite'))
    fireEvent.click(screen.getByRole('button', { name: /Download \.aa/ }))
    const mock = vi.mocked(downloadAAFile)
    expect(mock).toHaveBeenCalledTimes(1)
    const [filename, xml] = mock.mock.calls[0]
    expect(filename).toBe('Badbang_New_plan.aa')
    expect(xml).toContain('<aa game="eq2">')
    expect(xml).toContain('id="1" order="1" treeID="42"')
  })

  it('Save posts the current allocations and stores the share slug', async () => {
    const calls = stubFetch()
    renderPlanner()
    fireEvent.click(await screen.findByAltText('Leg Bite'))
    const save = screen.getByRole('button', { name: /Save as new/ })
    fireEvent.click(save)
    await waitFor(() => {
      const post = calls.find(c => c.init?.method === 'POST')
      expect(post).toBeTruthy()
      expect(JSON.parse(post!.init!.body as string).allocations).toEqual({ '42': { '1': 1 } })
    })
    // Saved → the share button appears
    expect(await screen.findByRole('button', { name: /Share link/ })).toBeInTheDocument()
  })
})
