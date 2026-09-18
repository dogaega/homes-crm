'use client'

import { useEffect, useState } from 'react'
import { supabase } from '@/lib/api'
import { useAuth } from '@/contexts/AuthContext'
import { AlertCircle, CheckCircle, RefreshCw } from 'lucide-react'

interface ConnectionStatus {
  isConnected: boolean
  lastChecked: Date
  error?: string
}

export default function ConnectionMonitor() {
  const { user, loading: authLoading } = useAuth()
  const [status, setStatus] = useState<ConnectionStatus>({
    isConnected: true,
    lastChecked: new Date()
  })
  const [isVisible, setIsVisible] = useState(false)

  const checkConnection = async () => {
    try {
      // Simple query to test connection
      const { error } = await supabase.from('agents').select('id').limit(1)

      // A 401 here means "not logged in" (or the session cookie hadn't
      // propagated yet on a fresh redirect), not a real connectivity
      // problem — this component previously treated the two the same,
      // which meant every fresh login briefly showed a scary "Connection
      // Issue / Unauthorized" banner that (a) was wrong, since the user
      // genuinely was connected, and (b) visually sat on top of the nav
      // bar, hiding the Reports link and the "Welcome back" greeting.
      const isAuthError = error?.code === '401'
      const realError = error && !isAuthError ? error : undefined

      setStatus({
        isConnected: !realError,
        lastChecked: new Date(),
        error: realError?.message
      })

      // Show notification only for a genuine (non-auth) error
      setIsVisible(!!realError)

      // Auto-hide success notifications
      if (!realError && isVisible) {
        setTimeout(() => setIsVisible(false), 3000)
      }
    } catch (err) {
      setStatus({
        isConnected: false,
        lastChecked: new Date(),
        error: err instanceof Error ? err.message : 'Unknown connection error'
      })
      setIsVisible(true)
    }
  }

  useEffect(() => {
    // Don't probe the connection until we actually know whether the user
    // is logged in — otherwise this races AuthContext's own session check
    // and can fire before the session cookie has propagated.
    if (authLoading || !user) return

    // Initial check
    checkConnection()

    // Check connection every 30 seconds
    const interval = setInterval(checkConnection, 30000)

    // Check on focus
    const handleFocus = () => checkConnection()
    window.addEventListener('focus', handleFocus)

    // Check on online/offline events
    const handleOnline = () => {
      console.log('Network online - checking connection')
      checkConnection()
    }
    const handleOffline = () => {
      setStatus(prev => ({
        ...prev,
        isConnected: false,
        error: 'Network offline'
      }))
      setIsVisible(true)
    }

    window.addEventListener('online', handleOnline)
    window.addEventListener('offline', handleOffline)

    return () => {
      clearInterval(interval)
      window.removeEventListener('focus', handleFocus)
      window.removeEventListener('online', handleOnline)
      window.removeEventListener('offline', handleOffline)
    }
  }, [authLoading, user])

  if (!isVisible) return null

  return (
    <div className={`fixed top-20 right-4 z-50 max-w-sm ${
      status.isConnected ? 'bg-green-50 border-green-200' : 'bg-red-50 border-red-200'
    } border rounded-lg shadow-lg p-4`}>
      <div className="flex items-start space-x-3">
        <div className="flex-shrink-0">
          {status.isConnected ? (
            <CheckCircle className="w-5 h-5 text-green-600" />
          ) : (
            <AlertCircle className="w-5 h-5 text-red-600" />
          )}
        </div>
        <div className="flex-1 min-w-0">
          <h3 className={`text-sm font-medium ${
            status.isConnected ? 'text-green-800' : 'text-red-800'
          }`}>
            {status.isConnected ? 'Connection Restored' : 'Connection Issue'}
          </h3>
          <p className={`text-sm ${
            status.isConnected ? 'text-green-700' : 'text-red-700'
          }`}>
            {status.isConnected 
              ? 'Successfully reconnected to the database'
              : status.error || 'Unable to connect to the database'
            }
          </p>
          <p className="text-xs text-gray-500 mt-1">
            Last checked: {status.lastChecked.toLocaleTimeString()}
          </p>
        </div>
        <div className="flex-shrink-0 flex space-x-2">
          <button
            onClick={checkConnection}
            className="text-gray-400 hover:text-gray-600 transition-colors"
            title="Check connection"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
          <button
            onClick={() => setIsVisible(false)}
            className="text-gray-400 hover:text-gray-600 transition-colors"
            title="Dismiss"
          >
            ×
          </button>
        </div>
      </div>
    </div>
  )
}