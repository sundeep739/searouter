import { useEffect, useState } from 'react'

/** True on phone-width viewports (<768px), kept in sync with resizes. */
export function useIsMobile(): boolean {
  const query = '(max-width: 767px)'
  const [isMobile, setIsMobile] = useState(
    () => typeof window !== 'undefined' && window.matchMedia(query).matches,
  )
  useEffect(() => {
    const mq = window.matchMedia(query)
    const onChange = () => setIsMobile(mq.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])
  return isMobile
}
