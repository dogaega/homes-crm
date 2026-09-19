'use client'

import { useEffect, useState, use, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { useAuth } from '@/contexts/AuthContext'
import { useLanguage } from '@/contexts/LanguageContext'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { supabase } from '@/lib/api'
import { Database } from '@/types/database'
import CommunicationTimeline from '@/components/clients/CommunicationTimeline'
import QuickActions from '@/components/clients/QuickActions'
import TaskTemplateApplicator from '@/components/tasks/TaskTemplateApplicator'
import ClientTasks from '@/components/clients/ClientTasks'
import ClientPropertyInterests from '@/components/clients/ClientPropertyInterests'
import { useHydration } from '@/hooks/useHydration'
import MainNavigation from '@/components/navigation/MainNavigation'
import BackNavigation from '@/components/navigation/BackNavigation'
import { 
  Edit, 
  Phone, 
  Mail, 
  MessageSquare, 
  MapPin, 
  Calendar,
  User,
  DollarSign,
  Building,
  Heart,
  ChevronDown,
  ChevronUp,
  FileText
} from 'lucide-react'

type Client = Database['public']['Tables']['clients']['Row']

interface ClientDetailPageProps {
  params: Promise<{ id: string }>
}

export default function ClientDetailPage({ params }: ClientDetailPageProps) {
  const { user, loading } = useAuth()
  const { t } = useLanguage()
  const router = useRouter()
  const isHydrated = useHydration()
  const { id } = use(params)
  const [client, setClient] = useState<Client | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [agentId, setAgentId] = useState<string>('')
  const [timelineKey, setTimelineKey] = useState(0)
  const [tasksKey, setTasksKey] = useState(0)
  const [isTasksExpanded, setIsTasksExpanded] = useState(false)
  const [isTimelineExpanded, setIsTimelineExpanded] = useState(false)
  const [isTemplatesExpanded, setIsTemplatesExpanded] = useState(false)
  const [isInterestsExpanded, setIsInterestsExpanded] = useState(false)

  useEffect(() => {
    if (!loading && !user) {
      router.push('/login')
    }
  }, [user, loading, router])

  const fetchClient = useCallback(async () => {
    try {
      setIsLoading(true)
      setError(null)
      
      // Get agent ID
      const { data: agent } = await supabase
        .from('agents')
        .select('id')
        .eq('user_id', user!.id)
        .single()

      if (!agent) {
        setError('Agent not found')
        return
      }

      setAgentId(agent.id)

      // Fetch client — team-wide shared visibility, not just this agent's own
      const { data: clientData, error: clientError } = await supabase
        .from('clients')
        .select('*')
        .eq('id', id)
        .single()

      if (clientError) {
        setError('Client not found')
        return
      }

      setClient(clientData)
    } catch (err) {
      console.error('Error:', err)
      setError('An unexpected error occurred')
    } finally {
      setIsLoading(false)
    }
  }, [user, id])

  useEffect(() => {
    if (user && id) {
      fetchClient()
    }
  }, [user, id]) // eslint-disable-line react-hooks/exhaustive-deps

  const getClientTypeIcon = (type: string) => {
    switch (type) {
      case 'buyer': return <User className="w-5 h-5" />
      case 'seller': return <Building className="w-5 h-5" />
      case 'renter': return <Heart className="w-5 h-5" />
      case 'investor': return <DollarSign className="w-5 h-5" />
      default: return <User className="w-5 h-5" />
    }
  }

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'active': return 'bg-green-100 text-green-800 border border-green-200'
      case 'prospect': return 'bg-blue-100 text-blue-800 border border-blue-200'
      case 'closed': return 'bg-gray-100 text-gray-800 border border-gray-200'
      case 'inactive': return 'bg-red-100 text-red-800 border border-red-200'
      default: return 'bg-gray-100 text-gray-800 border border-gray-200'
    }
  }

  const formatDate = (dateString: string) => {
    return new Date(dateString).toLocaleDateString()
  }

  const handleCommunicationLogged = () => {
    // Refresh the timeline and tasks by updating the keys
    setTimelineKey(prev => prev + 1)
    setTasksKey(prev => prev + 1)
  }

  // Show loading state until hydrated and auth is resolved
  if (!isHydrated || loading) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
      </div>
    )
  }

  // If not authenticated after hydration, show loading while redirecting
  if (!user) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
      </div>
    )
  }

  if (isLoading) {
    return (
      <div className="min-h-screen bg-gray-50">
        <MainNavigation title={t('clients.clientDetailsTitle')} />
        <main className="container mx-auto px-4 py-8">
          <BackNavigation fallbackPath="/clients" fallbackText={t('nav.clients')} />
          <div className="text-center py-12">
            <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 mx-auto mb-4"></div>
            <p className="text-gray-600">{t('clients.loadingClientDetails')}</p>
          </div>
        </main>
      </div>
    )
  }

  if (error || !client) {
    return (
      <div className="min-h-screen bg-gray-50">
        <MainNavigation title={t('clients.clientDetailsTitle')} />
        <main className="container mx-auto px-4 py-8">
          <BackNavigation fallbackPath="/clients" fallbackText={t('nav.clients')} />
          <div className="text-center py-12">
            <h2 className="text-2xl font-bold text-gray-900 mb-4">
              {error || t('clients.clientNotFound')}
            </h2>
            <Button onClick={() => router.push('/clients')}>
              {t('clients.returnToClients')}
            </Button>
          </div>
        </main>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <MainNavigation title={`${client.first_name} ${client.last_name}`} />
      
      <div className="flex items-center justify-between mb-6">
        <BackNavigation fallbackPath="/clients" fallbackText={t('nav.clients')} />
        <div className="flex items-center space-x-4">
          <div className="flex items-center space-x-3">
            {getClientTypeIcon(client.client_type || 'buyer')}
            <span className={`px-2 py-1 rounded-full text-xs font-medium ${getStatusColor(client.status || 'prospect')}`}>
              {client.status || 'prospect'}
            </span>
          </div>
          <div className="flex items-center space-x-3">
            <QuickActions
              clientId={client.id}
              agentId={agentId}
              clientName={`${client.first_name} ${client.last_name}`}
              clientEmail={client.email || undefined}
              clientPhone={client.phone || undefined}
              onCommunicationLogged={handleCommunicationLogged}
            />
            <Button variant="outline" size="sm">
              <Edit className="w-4 h-4 mr-2" />
              {t('common.edit')}
            </Button>
          </div>
        </div>
      </div>

      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
          {/* Client Information */}
          <div className="lg:col-span-1 space-y-6">
            <Card>
              <CardHeader>
                <CardTitle>{t('clients.contactInformation')}</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="flex items-center space-x-3">
                  <Mail className="w-4 h-4 text-gray-600" />
                  <span className="text-sm font-medium text-gray-900">{client.email}</span>
                </div>
                {client.phone && (
                  <div className="flex items-center space-x-3">
                    <Phone className="w-4 h-4 text-gray-600" />
                    <span className="text-sm font-medium text-gray-900">{client.phone}</span>
                  </div>
                )}
                {client.address && (
                  <div className="flex items-center space-x-3">
                    <MapPin className="w-4 h-4 text-gray-600" />
                    <span className="text-sm font-medium text-gray-900">{client.address}</span>
                  </div>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>{t('clients.clientDetailsTitle')}</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-semibold text-gray-900">{t('clients.type')}:</span>
                  <span className="text-sm font-medium text-gray-800">{client.client_type}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm font-semibold text-gray-900">{t('common.status')}:</span>
                  <span className={`px-2 py-1 rounded-full text-xs font-semibold ${getStatusColor(client.status || 'prospect')}`}>
                    {client.status || 'prospect'}
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm font-semibold text-gray-900">{t('clients.contactMethod')}:</span>
                  <span className="text-sm font-medium text-gray-800">{client.preferred_contact_method}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm font-semibold text-gray-900">{t('clients.source')}:</span>
                  <span className="text-sm font-medium text-gray-800">{client.source}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm font-semibold text-gray-900">{t('clients.created')}:</span>
                  <span className="text-sm font-medium text-gray-800">{client.created_at ? formatDate(client.created_at) : t('clients.unknown')}</span>
                </div>
              </CardContent>
            </Card>

            {client.budget_range && typeof client.budget_range === 'object' && (
              <Card>
                <CardHeader>
                  <CardTitle>{t('clients.budgetRange')}</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="text-lg font-bold text-gray-900">
                    {(client.budget_range as { min?: number; max?: number }).min?.toLocaleString() || t('common.none')}€ - {(client.budget_range as { min?: number; max?: number }).max?.toLocaleString() || t('common.none')}€
                  </div>
                </CardContent>
              </Card>
            )}

            {client.tags && client.tags.length > 0 && (
              <Card>
                <CardHeader>
                  <CardTitle>{t('clients.tags')}</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="flex flex-wrap gap-2">
                    {client.tags.map((tag, index) => (
                      <span
                        key={`client-${client.id}-tag-${tag}-${index}`}
                        className="bg-blue-100 text-blue-800 px-3 py-1 rounded-full text-sm font-semibold border border-blue-200"
                      >
                        {tag}
                      </span>
                    ))}
                  </div>
                </CardContent>
              </Card>
            )}

            {client.preferences && (
              <Card>
                <CardHeader>
                  <CardTitle>{t('clients.preferencesAndNotes')}</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="text-sm text-gray-800 font-medium whitespace-pre-wrap">
                    {typeof client.preferences === 'string' 
                      ? client.preferences 
                      : JSON.stringify(client.preferences, null, 2)
                    }
                  </div>
                </CardContent>
              </Card>
            )}
          </div>

          {/* Tasks and Communication Timeline */}
          <div className="lg:col-span-2 space-y-6">
            {/* Client Tasks */}
            <Card>
              <CardHeader 
                className="cursor-pointer hover:bg-gray-50 transition-colors"
                onClick={() => setIsTasksExpanded(!isTasksExpanded)}
              >
                <div className="flex items-center justify-between">
                  <CardTitle className="flex items-center space-x-2">
                    <MessageSquare className="w-5 h-5 text-blue-600" />
                    <span>{t('clients.clientTasks')}</span>
                  </CardTitle>
                  {isTasksExpanded ? (
                    <ChevronUp className="w-5 h-5 text-gray-500" />
                  ) : (
                    <ChevronDown className="w-5 h-5 text-gray-500" />
                  )}
                </div>
              </CardHeader>
              {isTasksExpanded && (
                <CardContent className="pt-0">
                  <ClientTasks
                    key={`client-tasks-${client.id}-${tasksKey}`}
                    clientId={client.id}
                    agentId={agentId}
                    clientName={`${client.first_name} ${client.last_name}`}
                  />
                </CardContent>
              )}
            </Card>
            
            {/* Communication Timeline */}
            <Card>
              <CardHeader 
                className="cursor-pointer hover:bg-gray-50 transition-colors"
                onClick={() => setIsTimelineExpanded(!isTimelineExpanded)}
              >
                <div className="flex items-center justify-between">
                  <CardTitle className="flex items-center space-x-2">
                    <Calendar className="w-5 h-5 text-green-600" />
                    <span>{t('clients.communicationHistory')}</span>
                  </CardTitle>
                  {isTimelineExpanded ? (
                    <ChevronUp className="w-5 h-5 text-gray-500" />
                  ) : (
                    <ChevronDown className="w-5 h-5 text-gray-500" />
                  )}
                </div>
              </CardHeader>
              {isTimelineExpanded && (
                <CardContent className="pt-0">
                  <CommunicationTimeline
                    key={`communication-timeline-${client.id}-${timelineKey}`}
                    clientId={client.id}
                    agentId={agentId}
                  />
                </CardContent>
              )}
            </Card>
            
            {/* Property Interests */}
            <Card>
              <CardHeader 
                className="cursor-pointer hover:bg-gray-50 transition-colors"
                onClick={() => setIsInterestsExpanded(!isInterestsExpanded)}
              >
                <div className="flex items-center justify-between">
                  <CardTitle className="flex items-center space-x-2">
                    <Heart className="w-5 h-5 text-red-600" />
                    <span>{t('clients.propertyInterests')}</span>
                  </CardTitle>
                  {isInterestsExpanded ? (
                    <ChevronUp className="w-5 h-5 text-gray-500" />
                  ) : (
                    <ChevronDown className="w-5 h-5 text-gray-500" />
                  )}
                </div>
              </CardHeader>
              {isInterestsExpanded && (
                <CardContent className="pt-0">
                  <ClientPropertyInterests
                    client={client}
                    currentAgentId={agentId}
                  />
                </CardContent>
              )}
            </Card>

            {/* Task Templates */}
            <Card>
              <CardHeader 
                className="cursor-pointer hover:bg-gray-50 transition-colors"
                onClick={() => setIsTemplatesExpanded(!isTemplatesExpanded)}
              >
                <div className="flex items-center justify-between">
                  <CardTitle className="flex items-center space-x-2">
                    <FileText className="w-5 h-5 text-purple-600" />
                    <span>{t('clients.applyTaskTemplates')}</span>
                  </CardTitle>
                  {isTemplatesExpanded ? (
                    <ChevronUp className="w-5 h-5 text-gray-500" />
                  ) : (
                    <ChevronDown className="w-5 h-5 text-gray-500" />
                  )}
                </div>
              </CardHeader>
              {isTemplatesExpanded && (
                <CardContent className="pt-0">
                  <TaskTemplateApplicator
                    key={`task-template-${client.id}`}
                    agentId={agentId}
                    entityType="client"
                    entityId={client.id}
                    entityName={`${client.first_name} ${client.last_name}`}
                  />
                </CardContent>
              )}
            </Card>
          </div>
        </div>
      </main>
    </div>
  )
} 