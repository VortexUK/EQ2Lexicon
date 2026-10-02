/**
 * /privacy — the privacy policy. Readable without a login (App.tsx
 * PUBLIC_PATHS). The text is derived from a code-level inventory of what
 * the site, the Discord bot and the two upload clients actually collect
 * (2026-09-28); keep it in step with the code, not the other way round.
 * The hosting-log retention figure is Railway's documented Pro-plan value
 * (docs.railway.com/guides/logs) — revisit it if the plan changes.
 */
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

export const PRIVACY_EFFECTIVE_DATE = '28 September 2026'
export const PRIVACY_CONTACT = 'tovortexuk@gmail.com'

const POLICY = `
**Effective:** ${PRIVACY_EFFECTIVE_DATE} · **Version:** 1.0

EQ2 Lexicon ("the site", "we") is a free, community-run companion site for the EverQuest II time-locked servers. It is operated by an individual in the United Kingdom, referred to here as the controller. Contact for anything in this policy, including data requests: **${PRIVACY_CONTACT}**.

This policy covers the website (all \`*.eq2lexicon.com\` subdomains), the EQ2 Lexicon Discord bot, and the two upload clients: the EQ2 Lexicon ACT plugin and EQ2Parser.

## 1. What we collect and why

### Your account
When you sign in with Discord we request only the \`identify\` scope. We store your Discord user id, display name, username and avatar hash, the time you first and last signed in, and your access status (pending, approved or denied). We do not request or store your email address, your Discord server list, or anything else from Discord.

**Why:** to know who you are between visits, gate access to an approved community, and attribute actions on the site (claims, uploads, edits) to a person.

### Character claims
You may claim EverQuest II characters as yours. We store the character name, the server, the claim status, who reviewed it and any note. Guild officers of that character's guild can see pending claims for their guild.

**Why:** claims link your Discord account to your in-game characters so uploads, rankings and officer permissions work.

### Uploaded combat data (parses)
The ACT plugin and EQ2Parser upload a per-fight **summary** of combat: encounter title, zone, timings, per-combatant damage, healing, deaths and ability breakdowns. They never upload raw log lines, chat, or anything outside the fight summary. Each upload is stored with the uploading character's name and the uploader's Discord id. The site shows the uploader's Discord display name to other logged-in users on the parse list and detail pages.

Uploads also carry a few **client warnings** about the upload itself: whether the game was writing the log at the time, and, from EQ2Parser, a bare flag when some program other than EverQuest II or ACT had the log file open. No program names are sent.

Uploads name **every character in the fight**, including people who do not use this site. For each named player we also freeze their level, class, guild and item level from the public Daybreak Census API at upload time. See section 6.

**Why:** parses, rankings and guild progression are the core of the site.

### Attendance
If a guild officer enables attendance tracking, EQ2Parser sends periodic snapshots naming the characters in the raid and the guild members who were online during the raid window. If the guild has linked its Discord server and a raid voice channel, the bot records the Discord ids of the members present in that voice channel while a raid session is live, every two minutes. Those ids include people who have never used this site. They are used for one thing: showing officers who was in voice but not in game. They are deleted after **90 days**.

**Why:** guild officers use attendance for raid management. Voice presence is an optional add-on a guild opts into through the bot.

### API tokens
Tokens you create for the upload clients are stored hashed. We keep the token's name, a short prefix for display, creation, last-use and revocation times.

### Other things you do on the site
Favourite characters, saved AA plans (optionally shared by link), raid availability, item-watch entries, guild raid schedules and settings, guild recruitment profiles and uploaded guild logos (each save and logo upload records which Discord account made it), role requests and the notes on them, and download counts for the clients. Officer actions record which Discord account took them.

### Server logs and security
Our application logs record, per request, a request id, your Discord id when signed in, and the server world. A small number of security-relevant events also record your **IP address and browser user agent**: an invalid or non-approved API token, a failed upload signature, a rejected malformed upload, and a client exceeding its upload budget. Our hosting provider's edge also keeps standard HTTP access logs (IP address, user agent, path, status, timing) for every request. We do not run analytics, advertising, or tracking scripts of any kind.

**Why:** running the service, abuse prevention and debugging.

### Monitoring
We export low-cardinality operational metrics (request counts and durations per route, cache and Census statistics, an *active users* count) to a monitoring service. No metric carries a user id, name or IP address.

## 2. Cookies and local storage
The site sets **one cookie**, \`session\`, which holds your signed login session for up to 14 days across all \`eq2lexicon.com\` subdomains. It is strictly necessary for signing in and there is no consent banner because there is nothing optional to consent to. The site also uses your browser's local storage for conveniences such as a shopping list or a saved simulator setup; that data never leaves your browser. Fonts are served from this site, not from a third party.

## 3. Lawful basis (UK GDPR / EU GDPR)
- **Legitimate interests** for running a community tool you have chosen to sign up to: your account, claims, uploads, rankings, the shared-fight data your guildmates upload, security logging and monitoring.
- **Legitimate interests of your guild**, balanced against yours, for attendance and officer tools that a guild officer enables.
- **Consent** where you actively opt in: sharing an AA plan by link, publishing a supporter listing, and, for guilds, enabling voice-channel presence through the bot. Withdraw by turning the feature off or asking us.

## 4. Who can see what
- **Logged-in members** can see character and guild data, uploaded parses including the uploader's Discord display name, rankings, and the names of supporters.
- **Guild officers** of your guild can additionally see claim requests for that guild and its attendance records.
- **Site administrators** can see everything above plus account status, role requests, tamper and quarantine reports (which include the full rejected upload and the uploader's identity), and audit logs.
- **Nothing on the site is visible without a login** except this policy.

## 5. Who we share data with
We do not sell data. We use these processors to run the service:

| Processor | What | Where |
|---|---|---|
| Discord | sign-in, the bot, your avatar image | USA |
| Daybreak Games (Census API) | our requests for character and guild data name the characters and guilds you look up or upload | USA |
| Railway | hosting the application and its logs | USA |
| Cloudflare R2 | continuous encrypted backups of our databases | global |
| Grafana Cloud | the operational metrics above | EU |
| GitHub | serving downloads and update checks for the clients | USA |

Data may therefore be processed outside the UK and EEA under those providers' standard contractual clauses.

## 6. People who do not use this site
Because uploads describe whole raids, the site holds in-game **character names** and Census-published statistics of players who have never signed in, and, where a guild has enabled it, the **Discord ids** of people present in a raid voice channel. Character names and Census statistics are public game data published by the game's operator. Voice presence is deleted after 90 days and never shown as an id. If you are one of these people and want a character or your Discord id removed from our records, contact us (section 8).

## 7. How long we keep things

| Data | Retention |
|---|---|
| Account, claims, tokens, favourites, plans, availability | until you delete your account or ask us to |
| Non-boss ("trash") fight uploads | 3 days |
| Boss-kill uploads and rankings | indefinitely, as the guild's raid record; the uploader's Discord identity is removed when that account is deleted |
| Duplicate uploads of one boss kill | collapsed to one after 3 days |
| Attendance (characters) | until an officer deletes the session |
| Voice-channel presence (Discord ids) | 90 days |
| Tamper and quarantine reports | until reviewed and purged by an administrator |
| Application and hosting logs | 30 days (Railway's retention on our plan) |
| Backups | rolling 7 days |
| Cached Census data | refreshed on use; not deleted on a schedule |

## 8. Your rights
You can **access** the personal data we hold about you, have it **corrected**, **object** to processing, ask for it to be **restricted**, and have it **erased**.

- **Delete your own account** at any time from Settings → API Tokens → *Delete my account*. This removes your account record and everything keyed to it, removes your Discord identity from your uploads (the fights stay as your guild's records), removes your voice-presence records and any reports about your uploads, and signs you out. Signing in again later creates a fresh account awaiting approval.
- **Everything else**, including requests about a character name or a Discord id that is not tied to an account, by email to **${PRIVACY_CONTACT}**. We answer within 30 days.
- You have the right to complain to the UK Information Commissioner's Office (ico.org.uk) or to your local EU data protection authority.

## 9. The upload clients
The ACT plugin and EQ2Parser run on your PC and send only what section 1 describes, over HTTPS, signed with your token. They read the server name from your log file's folder; they do not send your Windows username, install path, machine identifiers, or raw log lines. EQ2Parser keeps crash diagnostics in your local application data folder only; nothing is uploaded automatically. Both clients check GitHub for updates, which shares your IP address with GitHub.

## 10. Children
The site is for adults playing an online game and is not directed at children under 16.

## 11. Changes
We will update this page and its version number when the service changes. Significant changes will be announced on the community Discord.
`

export default function PrivacyPage() {
  return (
    <main className="max-w-[820px] mx-auto px-4 py-8">
      <h1 className="font-heading text-[1.7rem] text-gold m-0 mb-4">Privacy policy</h1>
      <article className="prose-lexicon text-[0.92rem] leading-relaxed text-text [&_h2]:font-heading [&_h2]:text-gold [&_h2]:text-[1.15rem] [&_h2]:mt-7 [&_h2]:mb-2 [&_h3]:text-text [&_h3]:text-[1rem] [&_h3]:mt-5 [&_h3]:mb-1 [&_p]:my-2 [&_ul]:my-2 [&_ul]:pl-5 [&_ul]:list-disc [&_li]:my-1 [&_a]:text-gold [&_a]:underline [&_table]:my-3 [&_table]:w-full [&_table]:text-[0.85rem] [&_th]:text-left [&_th]:text-text-muted [&_th]:font-semibold [&_th]:py-1 [&_th]:pr-3 [&_td]:py-1 [&_td]:pr-3 [&_td]:align-top [&_td]:border-t [&_td]:border-border [&_code]:font-mono [&_code]:text-[0.85em]">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{POLICY}</ReactMarkdown>
      </article>
    </main>
  )
}
