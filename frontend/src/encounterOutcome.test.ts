import { describe, expect, it } from 'vitest'

import { encounterOutcome, outcomeColor, outcomeLabel } from './encounterOutcome'

// ACT's GetEncounterSuccessLevel(): 1 = win, 2 = partial / unclear, 3 = loss.
// The site once showed 2 as "loss" (red) and 3 as "mixed" (amber); these pin
// the corrected mapping.
describe('encounterOutcome', () => {
  it('maps ACT success levels to outcomes', () => {
    expect(encounterOutcome(0)).toBe('unknown')
    expect(encounterOutcome(1)).toBe('win')
    expect(encounterOutcome(2)).toBe('partial')
    expect(encounterOutcome(3)).toBe('loss')
  })

  it('treats any other number as unknown', () => {
    expect(encounterOutcome(4)).toBe('unknown')
    expect(encounterOutcome(-1)).toBe('unknown')
    expect(encounterOutcome(Number.NaN)).toBe('unknown')
  })
})

describe('outcomeColor', () => {
  it('uses the success token for a win', () => {
    expect(outcomeColor(1, 'var(--text)')).toBe('var(--color-success)')
  })

  it('uses the warning token for 2 (partial), not danger', () => {
    expect(outcomeColor(2, 'var(--text)')).toBe('var(--color-warning)')
  })

  it('uses the danger token for 3 (loss), not warning', () => {
    expect(outcomeColor(3, 'var(--text)')).toBe('var(--color-danger)')
  })

  it("falls back to the caller's colour when the outcome is unknown", () => {
    expect(outcomeColor(0, 'var(--gold)')).toBe('var(--gold)')
    expect(outcomeColor(7, 'var(--text)')).toBe('var(--text)')
  })
})

describe('outcomeLabel', () => {
  it('labels 2 "partial" and 3 "loss"', () => {
    expect(outcomeLabel(1)).toBe('win')
    expect(outcomeLabel(2)).toBe('partial')
    expect(outcomeLabel(3)).toBe('loss')
  })

  it('shows a dash when unknown', () => {
    expect(outcomeLabel(0)).toBe('—')
    expect(outcomeLabel(9)).toBe('—')
  })
})
