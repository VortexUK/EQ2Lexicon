import { describe, expect, it } from 'vitest'

import { clientWarningLevel, warningsNeedingAttention } from './clientWarnings'

// EQ2Parser stamps `log_writer_eq2` on every automatic upload when the game
// itself held the log: the good case. The admin chip once fired on it, so it
// fired on every upload from the desktop parser.
describe('clientWarningLevel', () => {
  it('reads log_writer_eq2 (the game held the log) as benign', () => {
    expect(clientWarningLevel('log_writer_eq2')).toBe('benign')
  })

  it.each(['log_writer_unverified', 'log_foreign_holder', 'folder_hint_mismatch'])(
    'reads %s as needing attention',
    code => {
      expect(clientWarningLevel(code)).toBe('attention')
    },
  )

  it('reads a value no client sends today as needing attention', () => {
    expect(clientWarningLevel('brand_new_signal_v2')).toBe('attention')
  })

  it('matches exactly, as stored: a near miss is not the benign value', () => {
    expect(clientWarningLevel('LOG_WRITER_EQ2')).toBe('attention')
    expect(clientWarningLevel('log_writer_eq2 ')).toBe('attention')
    expect(clientWarningLevel('log_writer_eq2_x')).toBe('attention')
    expect(clientWarningLevel('')).toBe('attention')
  })
})

describe('warningsNeedingAttention', () => {
  it('is empty for an upload with no warnings', () => {
    expect(warningsNeedingAttention(null)).toEqual([])
    expect(warningsNeedingAttention(undefined)).toEqual([])
    expect(warningsNeedingAttention([])).toEqual([])
  })

  it('is empty when the only stamp is the benign one', () => {
    expect(warningsNeedingAttention(['log_writer_eq2'])).toEqual([])
  })

  it('keeps the attention values and drops the benign one, in the order sent', () => {
    expect(warningsNeedingAttention(['log_writer_eq2', 'log_foreign_holder'])).toEqual(['log_foreign_holder'])
    expect(warningsNeedingAttention(['log_foreign_holder', 'log_writer_unverified'])).toEqual([
      'log_foreign_holder',
      'log_writer_unverified',
    ])
  })

  it('keeps an unknown value', () => {
    expect(warningsNeedingAttention(['log_writer_eq2', 'brand_new_signal_v2'])).toEqual(['brand_new_signal_v2'])
  })

  it('does not change the list it is given', () => {
    const sent = ['log_writer_eq2', 'folder_hint_mismatch']
    warningsNeedingAttention(sent)
    expect(sent).toEqual(['log_writer_eq2', 'folder_hint_mismatch'])
  })
})
