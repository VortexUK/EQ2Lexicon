/**
 * TEMP PROD DIAG v6 — comprehensive History + Location API monkey-patch.
 *
 * v5 broke the site by trying `window.location.assign = function(){}` —
 * the Location instance's assign is read-only per spec. v6 fixes that by
 * using Object.defineProperty against the PROTOTYPE for every override,
 * AND wraps every step in try/catch so a single failing patch can't take
 * the whole page down.
 *
 * Imported FIRST in main.tsx so patches install before any library can
 * cache a reference to the originals.
 *
 * Revert by deleting this file + removing the import line in main.tsx.
 */

const TAG = '[history-trace v2026-05-29-prod-diag-v6]'

if (typeof window !== 'undefined') {
  console.warn(`${TAG} installing — patching History + Location APIs`)

  const trace = (label: string, ...args: unknown[]) => {
    console.warn(`${TAG} ${label}`, ...args, new Error().stack)
  }

  // Wrap any prototype-method override in a safe block. Failures are logged
  // but do not abort the rest of the installation. CRITICAL: do NOT bind
  // `orig` to the prototype — it must be called with the original `this`
  // (the History or Location INSTANCE), otherwise host implementations
  // (jsdom, browser internals) throw 'called on an object that is not a
  // valid instance'.
  function patchMethod<T extends object>(
    proto: T,
    name: keyof T,
    wrap: (orig: (this: unknown, ...args: unknown[]) => unknown) => (this: unknown, ...args: unknown[]) => unknown,
  ): void {
    try {
      const desc = Object.getOwnPropertyDescriptor(proto, name)
      if (!desc || typeof desc.value !== 'function') {
        console.warn(`${TAG} skip ${String(name)} — not a method`)
        return
      }
      const orig = desc.value as (this: unknown, ...args: unknown[]) => unknown
      Object.defineProperty(proto, name, {
        configurable: true,
        enumerable: desc.enumerable,
        writable: true,
        value: wrap(orig),
      })
    } catch (e) {
      console.warn(`${TAG} FAILED to patch ${String(name)}:`, e)
    }
  }

  // Wrap any prototype-setter override safely.
  function patchSetter<T extends object>(proto: T, name: string): void {
    try {
      const desc = Object.getOwnPropertyDescriptor(proto, name)
      if (!desc || !desc.get || !desc.set) {
        console.warn(`${TAG} skip ${name} setter — no get/set descriptor`)
        return
      }
      const origGet = desc.get
      const origSet = desc.set
      Object.defineProperty(proto, name, {
        configurable: true,
        enumerable: desc.enumerable,
        get() { return origGet.call(this) },
        set(v: unknown) {
          trace(`location.${name} =`, v)
          return origSet.call(this, v)
        },
      })
    } catch (e) {
      console.warn(`${TAG} FAILED to patch ${name} setter:`, e)
    }
  }

  // ── History.prototype methods ──────────────────────────────────────────────
  const HP = History.prototype
  patchMethod(HP, 'pushState', (orig) => function (this: unknown, ...args: unknown[]) {
    trace('History.pushState →', args[2])
    return orig.apply(this, args)
  })
  patchMethod(HP, 'replaceState', (orig) => function (this: unknown, ...args: unknown[]) {
    trace('History.replaceState →', args[2])
    return orig.apply(this, args)
  })
  patchMethod(HP, 'go', (orig) => function (this: unknown, ...args: unknown[]) {
    trace('History.go →', args[0])
    return orig.apply(this, args)
  })
  patchMethod(HP, 'back', (orig) => function (this: unknown, ...args: unknown[]) {
    trace('History.back')
    return orig.apply(this, args)
  })
  patchMethod(HP, 'forward', (orig) => function (this: unknown, ...args: unknown[]) {
    trace('History.forward')
    return orig.apply(this, args)
  })

  // ── Location.prototype methods ─────────────────────────────────────────────
  // Use Object.getPrototypeOf(location) rather than `Location.prototype`
  // because the latter is not always exposed as a global.
  const LP = Object.getPrototypeOf(window.location) as object
  patchMethod(LP, 'assign' as never, (orig) => function (this: unknown, ...args: unknown[]) {
    trace('location.assign →', args[0])
    return orig.apply(this, args)
  })
  patchMethod(LP, 'replace' as never, (orig) => function (this: unknown, ...args: unknown[]) {
    trace('location.replace →', args[0])
    return orig.apply(this, args)
  })

  // ── Location.prototype setters ─────────────────────────────────────────────
  for (const prop of ['href', 'hash', 'search', 'pathname'] as const) {
    patchSetter(LP, prop)
  }

  ;(window as unknown as { __historyTracedV6?: boolean }).__historyTracedV6 = true
  console.warn(`${TAG} installed — window.__historyTracedV6 = true`)
}

export {}
