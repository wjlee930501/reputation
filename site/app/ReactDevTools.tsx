'use client'

import { useEffect } from 'react'

/** Development-only visual inspection helpers; this component renders no product UI. */
export function ReactDevTools() {
  useEffect(() => {
    if (
      process.env.NODE_ENV !== 'development' ||
      process.env.NEXT_PUBLIC_DISABLE_REACT_DEVTOOLS === '1'
    ) {
      return
    }

    void import('react-grab')
    void import('react-scan').then(({ scan }) => scan({ enabled: true }))
  }, [])

  return null
}
