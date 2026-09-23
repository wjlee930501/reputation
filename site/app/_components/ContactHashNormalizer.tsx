'use client'

import { useEffect } from 'react'

import { CONTACT_HASH, normalizeContactHash } from '@/lib/contact-hash'

const CONTACT_ID = CONTACT_HASH.slice(1)

export default function ContactHashNormalizer() {
  useEffect(() => {
    const normalizeAndScroll = () => {
      const normalizedHash = normalizeContactHash(window.location.hash)
      if (!normalizedHash) return

      const url = new URL(window.location.href)
      url.hash = normalizedHash
      window.history.replaceState(window.history.state, '', url)

      const contact = document.getElementById(CONTACT_ID)
      contact?.scrollIntoView()
      contact?.focus({ preventScroll: true })
    }

    normalizeAndScroll()
    window.addEventListener('hashchange', normalizeAndScroll)
    return () => window.removeEventListener('hashchange', normalizeAndScroll)
  }, [])

  return null
}
