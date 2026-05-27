/**
 * Countdown timer to the EQ2 server launch.
 * Sources the launch date from useServer() — populated by /api/server.
 * Hides automatically once the launch time has passed or if no date is set.
 */
import { Fragment, useEffect, useState } from 'react'
import { useServer } from '../hooks/useServer'

function pad(n: number) {
  return String(n).padStart(2, '0')
}

export default function ServerLaunchTimer() {
  const server = useServer()
  const [launchMs, setLaunchMs] = useState<number | null>(null)
  const [timeLeft, setTimeLeft] = useState(0)

  // Derive launchMs from the server context whenever it loads or changes
  useEffect(() => {
    const dt = server?.launchDt ?? null
    if (!dt) return
    const ms = new Date(dt).getTime()
    if (!isNaN(ms) && ms > Date.now()) {
      setLaunchMs(ms)
      setTimeLeft(ms - Date.now())
    }
  }, [server?.launchDt])

  // Tick every second once we have a launch time
  useEffect(() => {
    if (launchMs === null) return
    const id = setInterval(() => {
      setTimeLeft(Math.max(0, launchMs - Date.now()))
    }, 1000)
    return () => clearInterval(id)
  }, [launchMs])

  if (launchMs === null || timeLeft <= 0) return null

  const days    = Math.floor(timeLeft / 86_400_000)
  const hours   = Math.floor((timeLeft % 86_400_000) / 3_600_000)
  const minutes = Math.floor((timeLeft % 3_600_000) / 60_000)
  const seconds = Math.floor((timeLeft % 60_000) / 1_000)

  // Human-readable date line derived from the JS Date object
  const launchDate = new Date(launchMs)
  const dateLabel = launchDate.toLocaleDateString('en-GB', {
    day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC',
  }) + ' · ' + launchDate.toLocaleTimeString('en-GB', {
    hour: '2-digit', minute: '2-digit', timeZone: 'UTC', timeZoneName: 'short',
  })

  const units = [
    { value: days,    label: 'Days'    },
    { value: hours,   label: 'Hours'   },
    { value: minutes, label: 'Minutes' },
    { value: seconds, label: 'Seconds' },
  ]

  return (
    <div
      className="mt-6 mx-auto max-w-[500px] pt-[1.4rem] px-7 pb-5 rounded-[10px] text-center"
      style={{
        background: 'linear-gradient(180deg, rgba(30,24,15,0.85) 0%, rgba(18,14,8,0.92) 100%)',
        border: '1px solid rgba(var(--gold-rgb), 0.3)',
        boxShadow: '0 0 32px rgba(var(--gold-rgb), 0.07), inset 0 1px 0 rgba(var(--gold-rgb), 0.12)',
      }}
    >

      {/* Eyebrow */}
      <div
        className="font-heading text-[0.68rem] font-semibold tracking-[0.2em] uppercase mb-[0.3rem]"
        style={{ color: 'rgba(var(--gold-rgb), 0.55)' }}
      >
        ✦ &nbsp; Server Launch &nbsp; ✦
      </div>

      {/* Heading */}
      <div
        className="font-heading text-[1.05rem] font-bold tracking-[0.05em] inline-block mb-[1.2rem]"
        style={{
          background: 'linear-gradient(135deg, var(--gold) 0%, var(--gold-bright) 50%, var(--gold) 100%)',
          WebkitBackgroundClip: 'text',
          WebkitTextFillColor: 'transparent',
          backgroundClip: 'text',
        }}
      >
        Norrath Awakens In…
      </div>

      {/* Countdown units */}
      <div className="flex justify-center gap-[0.6rem]">
        {units.map(({ value, label }, i) => (
          <Fragment key={label}>
            <div className="flex flex-col items-center gap-[0.3rem]">
              <div
                className="font-heading text-[2rem] font-bold leading-none min-w-[2.4ch] py-[0.45rem] px-[0.55rem] rounded-[6px] text-gold-bright tracking-[0.05em]"
                style={{
                  background: 'rgba(var(--gold-rgb), 0.07)',
                  border: '1px solid rgba(var(--gold-rgb), 0.22)',
                  textShadow: '0 0 18px rgba(var(--gold-rgb), 0.5)',
                }}
              >
                {pad(value)}
              </div>
              <div
                className="text-[0.58rem] tracking-[0.16em] uppercase font-semibold"
                style={{ color: 'rgba(var(--gold-rgb), 0.45)' }}
              >
                {label}
              </div>
            </div>
            {/* Separator between units, not after last */}
            {i < units.length - 1 && (
              <div
                className="self-start pt-[0.55rem] text-[1.4rem] leading-none font-light"
                style={{ color: 'rgba(var(--gold-rgb), 0.25)' }}
              >
                :
              </div>
            )}
          </Fragment>
        ))}
      </div>

      {/* Date line */}
      <div
        className="mt-4 text-[0.72rem] tracking-[0.1em] font-heading"
        style={{ color: 'rgba(var(--gold-rgb), 0.4)' }}
      >
        {dateLabel}
      </div>

    </div>
  )
}
