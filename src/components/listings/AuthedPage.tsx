'use client'

import { useEffect, type ReactNode } from 'react'
import { useRouter } from 'next/navigation'
import { useAuth } from '@/contexts/AuthContext'
import { useHydration } from '@/hooks/useHydration'
import MainNavigation from '@/components/navigation/MainNavigation'

// Login guard + navigation shared by the market listings and requests pages.
export default function AuthedPage({ title, children }: { title: string; children: ReactNode }) {
  const { user, loading } = useAuth()
  const router = useRouter()
  const isHydrated = useHydration()

  useEffect(() => {
    if (!loading && !user) router.push('/login')
  }, [user, loading, router])

  if (!isHydrated || loading || !user) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
      </div>
    )
  }
  return (
    <div className="min-h-screen bg-background">
      <MainNavigation title={title} />
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">{children}</main>
    </div>
  )
}
