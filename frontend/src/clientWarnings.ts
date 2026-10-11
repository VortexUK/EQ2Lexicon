// One source of truth for how an upload's `client_warnings` are read. The
// admin parses table decides from here whether a parse gets the warning chip.
// Do not re-declare the lists in a page.
//
// `client_warnings` are soft signals the uploading client attaches to an
// upload that is still accepted. The server stores them as sent and never
// interprets them (backend: IngestRequest.client_warnings); the meaning lives
// here, for display only. Values in use, mirrored with the two clients:
//
//   EQ2Parser stamps log provenance on EVERY automatic upload, exactly one of:
//     log_writer_eq2        the game itself was seen holding the log open.
//                           This is the GOOD case, so it is not a warning.
//     log_writer_unverified the game was not seen holding the log (a backlog
//                           parse, for example): worth a look.
//   and, in addition, when it applies:
//     log_foreign_holder    some program other than the game had the log
//                           open: worth a look.
//   ACT plugin:
//     folder_hint_mismatch  ACT's folder hint disagreed with the detected
//                           server: worth a look.
//
// Anything not listed as benign needs attention, so a value a future client
// starts sending shows up instead of being hidden.

export type ClientWarningLevel = 'benign' | 'attention'

const BENIGN_CLIENT_WARNINGS: ReadonlySet<string> = new Set(['log_writer_eq2'])

/** How one stored value is read. Matched exactly, as stored. */
export function clientWarningLevel(code: string): ClientWarningLevel {
  return BENIGN_CLIENT_WARNINGS.has(code) ? 'benign' : 'attention'
}

/**
 * The values that need an admin's attention, in the order sent. Empty when the
 * upload carried none, or only benign ones: no chip is shown then.
 */
export function warningsNeedingAttention(warnings: readonly string[] | null | undefined): string[] {
  return (warnings ?? []).filter(code => clientWarningLevel(code) === 'attention')
}
