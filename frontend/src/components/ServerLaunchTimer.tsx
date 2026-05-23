/**
 * Countdown timer to the EQ2 server launch.
 * Hides automatically once the launch time has passed.
 */
import { Fragment, useEffect, useState } from 'react'

const LAUNCH_UTC = new Date('2026-06-09T20:00:00Z').getTime()

function pad(n: number) {
  return String(n).padStart(2, '0')
}

export default function ServerLaunchTimer() {
  const [timeLeft, setTimeLeft] = useState(() => Math.max(0, LAUNCH_UTC - Date.now()))

  useEffect(() => {
    const id = setInterval(() => {
      setTimeLeft(Math.max(0, LAUNCH_UTC - Date.now()))
    }, 1000)
    return () => clearInterval(id)
  }, [])

  if (timeLeft <= 0) return null

  const days    = Math.floor(timeLeft / 86_400_000)
  const hours   = Math.floor((timeLeft % 86_400_000) / 3_600_000)
  const minutes = Math.floor((timeLeft % 3_600_000) / 60_000)
  const seconds = Math.floor((timeLeft % 60_000) / 1_000)

  const units = [
    { value: days,    label: 'Days'    },
    { value: hours,   label: 'Hours'   },
    { value: minutes, label: 'Minutes' },
    { value: seconds, label: 'Seconds' },
  ]

  return (
    <div style={{
      margin: '1.5rem auto 0',
      maxWidth: 500,
      padding: '1.4rem 1.75rem 1.25rem',
      background: 'linear-gradient(180deg, rgba(30,24,15,0.85) 0%, rgba(18,14,8,0.92) 100%)',
      border: '1px solid rgba(200,169,110,0.3)',
      borderRadius: 10,
      boxShadow: '0 0 32px rgba(200,169,110,0.07), inset 0 1px 0 rgba(200,169,110,0.12)',
      textAlign: 'center',
    }}>

      {/* Eyebrow */}
      <div style={{
        fontFamily: "'Cinzel', serif",
        fontSize: '0.68rem',
        fontWeight: 600,
        letterSpacing: '0.2em',
        textTransform: 'uppercase',
        color: 'rgba(200,169,110,0.55)',
        marginBottom: '0.3rem',
      }}>
        ✦ &nbsp; Server Launch &nbsp; ✦
      </div>

      {/* Heading */}
      <div style={{
        fontFamily: "'Cinzel', serif",
        fontSize: '1.05rem',
        fontWeight: 700,
        letterSpacing: '0.05em',
        background: 'linear-gradient(135deg, #c8a96e 0%, #e8d5a3 50%, #c8a96e 100%)',
        WebkitBackgroundClip: 'text',
        WebkitTextFillColor: 'transparent',
        backgroundClip: 'text',
        display: 'inline-block',
        marginBottom: '1.2rem',
      }}>
        Norrath Awakens In…
      </div>

      {/* Countdown units */}
      <div style={{ display: 'flex', justifyContent: 'center', gap: '0.6rem' }}>
        {units.map(({ value, label }, i) => (
          <Fragment key={label}>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '0.3rem' }}>
              <div style={{
                fontFamily: "'Cinzel', serif",
                fontSize: '2rem',
                fontWeight: 700,
                lineHeight: 1,
                minWidth: '2.4ch',
                padding: '0.45rem 0.55rem',
                background: 'rgba(200,169,110,0.07)',
                border: '1px solid rgba(200,169,110,0.22)',
                borderRadius: 6,
                color: '#e8d5a3',
                textShadow: '0 0 18px rgba(200,169,110,0.5)',
                letterSpacing: '0.05em',
              }}>
                {pad(value)}
              </div>
              <div style={{
                fontSize: '0.58rem',
                letterSpacing: '0.16em',
                textTransform: 'uppercase',
                color: 'rgba(200,169,110,0.45)',
                fontWeight: 600,
              }}>
                {label}
              </div>
            </div>
            {/* Separator between units, not after last */}
            {i < units.length - 1 && (
              <div style={{
                alignSelf: 'flex-start',
                paddingTop: '0.55rem',
                fontSize: '1.4rem',
                color: 'rgba(200,169,110,0.25)',
                lineHeight: 1,
                fontWeight: 300,
              }}>
                :
              </div>
            )}
          </Fragment>
        ))}
      </div>

      {/* Date line */}
      <div style={{
        marginTop: '1rem',
        fontSize: '0.72rem',
        color: 'rgba(200,169,110,0.4)',
        letterSpacing: '0.1em',
        fontFamily: "'Cinzel', serif",
      }}>
        June 9th, 2026 · 20:00 UTC
      </div>

    </div>
  )
}
