export function fmtTransit(hours: number | null | undefined): string {
  if (hours == null) return '—'
  if (hours < 48) return `${hours.toFixed(1)} h`
  return `${(hours / 24).toFixed(1)} days`
}

export function fmtNm(nm: number): string {
  return nm.toLocaleString(undefined, { maximumFractionDigits: 0 }) + ' nm'
}

export function eta(departure: Date, hours: number): string {
  const d = new Date(departure.getTime() + hours * 3600_000)
  return d.toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

/** datetime-local input value for a date (local time, minutes precision). */
export function toLocalInput(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}
