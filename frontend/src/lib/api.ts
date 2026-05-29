/**
 * handle — generic fetch response handler. Throws on non-ok responses,
 * returns the parsed JSON otherwise. Used by hand-rolled fetches that
 * don't go through useFetch (e.g. mutation endpoints invoked from event
 * handlers).
 */
export async function handle<T>(r: Response): Promise<T> {
  if (!r.ok) {
    const body = await r.json().catch(() => ({}))
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${r.status}`)
  }
  return r.json() as Promise<T>
}
