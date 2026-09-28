import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

import PrivacyPage, { PRIVACY_CONTACT, PRIVACY_EFFECTIVE_DATE } from './PrivacyPage'

describe('PrivacyPage', () => {
  it('renders the policy with the contact, the date, and the sections a regulator looks for', () => {
    render(
      <MemoryRouter>
        <PrivacyPage />
      </MemoryRouter>,
    )
    expect(screen.getByRole('heading', { level: 1, name: 'Privacy policy' })).toBeInTheDocument()
    expect(screen.getAllByText(new RegExp(PRIVACY_CONTACT)).length).toBeGreaterThan(0)
    expect(screen.getByText(new RegExp(PRIVACY_EFFECTIVE_DATE))).toBeInTheDocument()
    for (const heading of [
      'What we collect and why',
      'Cookies and local storage',
      'Lawful basis',
      'Who we share data with',
      'People who do not use this site',
      'How long we keep things',
      'Your rights',
    ]) {
      expect(screen.getByRole('heading', { level: 2, name: new RegExp(heading) })).toBeInTheDocument()
    }
    // The two retention promises the code enforces.
    expect(screen.getByText(/Voice-channel presence \(Discord ids\)/)).toBeInTheDocument()
    expect(screen.getAllByText(/90 days/).length).toBeGreaterThan(0)
  })
})
