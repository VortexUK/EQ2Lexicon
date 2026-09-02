/**
 * Countdown timer card — the gilded "Norrath Awakens In…" banner.
 *
 * Default export counts down to the EQ2 server launch (launchDt from
 * useServer(), shown on the login gate). The parameterized CountdownCard is
 * reused by XpacLaunchBanner for upcoming-expansion releases. Hides
 * automatically once the target time has passed or if no date is set.
 */
import { Fragment, useEffect, useState } from 'react'
import { useServer } from '../hooks/useServer'

function pad(n: number) {
  return String(n).padStart(2, '0')
}

export function CountdownCard({
  targetIso,
  eyebrow,
  heading,
  localTime = false,
}: {
  targetIso: string | null
  eyebrow: string
  heading: string
  /** Show the date line in the viewer's timezone instead of UTC. */
  localTime?: boolean
}) {
  const [launchMs, setLaunchMs] = useState<number | null>(null)
  const [timeLeft, setTimeLeft] = useState(0)

  // Derive launchMs from the target whenever it loads or changes
  useEffect(() => {
    if (!targetIso) return
    const ms = new Date(targetIso).getTime()
    if (!isNaN(ms) && ms > Date.now()) {
      setLaunchMs(ms)
      setTimeLeft(ms - Date.now())
    }
  }, [targetIso])

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
  const tz = localTime ? undefined : 'UTC'
  const dateLabel = launchDate.toLocaleDateString('en-GB', {
    day: 'numeric', month: 'long', year: 'numeric', timeZone: tz,
  }) + ' · ' + launchDate.toLocaleTimeString('en-GB', {
    hour: '2-digit', minute: '2-digit', timeZone: tz, timeZoneName: 'short',
  })

  const units = [
    { value: days,    label: 'Days'    },
    { value: hours,   label: 'Hours'   },
    { value: minutes, label: 'Minutes' },
    { value: seconds, label: 'Seconds' },
  ]

  return (
    <div
      className="mt-6 mx-auto max-w-[500px] pt-[1.4rem] px-7 pb-5 rounded-lg text-center"
      style={{
        background: 'linear-gradient(180deg, rgba(30,24,15,0.85) 0%, rgba(18,14,8,0.92) 100%)',
        border: '1px solid rgba(var(--gold-rgb), 0.3)',
        boxShadow: '0 0 32px rgba(var(--gold-rgb), 0.07), inset 0 1px 0 rgba(var(--gold-rgb), 0.12)',
      }}
    >

      {/* Eyebrow */}
      <div
        className="font-heading text-[0.68rem] font-semibold tracking-[0.2em] uppercase mb-1"
        style={{ color: 'rgba(var(--gold-rgb), 0.55)' }}
      >
        ✦ &nbsp; {eyebrow} &nbsp; ✦
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
        {heading}
      </div>

      {/* Countdown units */}
      <div className="flex justify-center gap-2.5">
        {units.map(({ value, label }, i) => (
          <Fragment key={label}>
            <div className="flex flex-col items-center gap-1">
              <div
                className="font-heading text-[2rem] font-bold leading-none min-w-[2.4ch] py-2 px-2 rounded-sm2 text-gold-bright tracking-[0.05em]"
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

/** The login-gate countdown to the server launch (the original banner). */
export default function ServerLaunchTimer() {
  const server = useServer()
  return (
    <CountdownCard
      targetIso={server?.launchDt ?? null}
      eyebrow="Server Launch"
      heading="Norrath Awakens In…"
    />
  )
}

/**
 * Home-page countdown to the next expansion release. Driven by the
 * admin-editable next_xpac / next_xpac_dt on the servers registry —
 * renders nothing until both are set, and hides itself once the release
 * time passes. Shows the release moment in the viewer's local timezone.
 */
export function XpacLaunchBanner() {
  const server = useServer()
  if (!server?.nextXpac || !server?.nextXpacDt) return null
  return (
    <CountdownCard
      targetIso={server.nextXpacDt}
      eyebrow="Expansion Launch"
      heading={`${server.nextXpac} Arrives In…`}
      localTime
    />
  )
}
