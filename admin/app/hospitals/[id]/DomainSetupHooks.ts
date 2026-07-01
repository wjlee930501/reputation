import { useEffect, useState } from 'react'

import type { Dispatch, SetStateAction } from 'react'
import type { DomainDnsStrategy, DomainManagementMode } from '@/lib/domain'
import { getDomainProfileResetState } from '@/lib/domain-profile-reset'
import type { DomainProfile } from './DomainSetupTypes'

interface ResetArgs {
  hospitalId: string
  profile: DomainProfile
  setDomainSavedValue: Dispatch<SetStateAction<string>>
  setManagementMode: Dispatch<SetStateAction<DomainManagementMode>>
  setDnsStrategy: Dispatch<SetStateAction<DomainDnsStrategy>>
  setRegistrar: Dispatch<SetStateAction<string>>
  setDnsProvider: Dispatch<SetStateAction<string>>
  setPurchaseNote: Dispatch<SetStateAction<string>>
}

export function useDomainProfileReset({
  hospitalId,
  profile,
  setDomainSavedValue,
  setManagementMode,
  setDnsStrategy,
  setRegistrar,
  setDnsProvider,
  setPurchaseNote,
}: ResetArgs) {
  const [loadedProfileKey, setLoadedProfileKey] = useState<string | null>(null)

  useEffect(() => {
    const resetState = getDomainProfileResetState(hospitalId, profile, loadedProfileKey)
    if (!resetState) return
    setDomainSavedValue(resetState.domainSavedValue)
    setManagementMode(resetState.managementMode)
    setDnsStrategy(resetState.dnsStrategy)
    setRegistrar(resetState.registrar)
    setDnsProvider(resetState.dnsProvider)
    setPurchaseNote(resetState.purchaseNote)
    setLoadedProfileKey(resetState.profileKey)
  }, [
    hospitalId,
    loadedProfileKey,
    profile,
    setDnsProvider,
    setDnsStrategy,
    setDomainSavedValue,
    setManagementMode,
    setPurchaseNote,
    setRegistrar,
  ])
}
