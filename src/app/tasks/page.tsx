'use client'

import { useAuth } from '@/contexts/AuthContext'
import { useLanguage } from '@/contexts/LanguageContext'
import { useRouter } from 'next/navigation'
import { useEffect, useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { supabase } from '@/lib/api'
import { Database } from '@/types/database'
import TaskTemplateManager from '@/components/tasks/TaskTemplateManager'
import { useHydration } from '@/hooks/useHydration'
import MainNavigation from '@/components/navigation/MainNavigation'
import Pagination from '@/components/ui/Pagination'
import { 
  CheckSquare, 
  Plus, 
  Filter, 
  Search, 
  Calendar, 
  Clock,
  AlertCircle,
  User,
  Home,
  FileText,
  Settings,
  X,
  MessageCircle,
  MapPin,
  DollarSign,
  Phone,
  Mail,
  CalendarDays
} from 'lucide-react'

type Task = Database['public']['Tables']['tasks']['Row']
type TaskComment = Database['public']['Tables']['task_comments']['Row']
type Client = Database['public']['Tables']['clients']['Row']
type Property = Database['public']['Tables']['properties']['Row']

interface EnhancedTask extends Task {
  client_name?: string
  property_address?: string
}

interface TaskWithDetails extends Task {
  task_comments: TaskComment[]
  client?: Client
  property?: Property
}

export default function TasksPage() {
  const { user, loading } = useAuth()
  const { t } = useLanguage()
  const router = useRouter()
  const isHydrated = useHydration()
  const [tasks, setTasks] = useState<EnhancedTask[]>([])
  const [filteredTasks, setFilteredTasks] = useState<EnhancedTask[]>([])
  const [paginatedTasks, setPaginatedTasks] = useState<EnhancedTask[]>([])
  const [searchTerm, setSearchTerm] = useState('')
  const [selectedStatus, setSelectedStatus] = useState<string>('all')
  const [selectedPriority, setSelectedPriority] = useState<string>('all')
  const [selectedRecency, setSelectedRecency] = useState<string>('all')
  const [isLoading, setIsLoading] = useState(true)
  const [agentId, setAgentId] = useState<string>('')
  
  // Pagination state
  const [currentPage, setCurrentPage] = useState(1)
  const [itemsPerPage, setItemsPerPage] = useState(25)
  const [totalItems, setTotalItems] = useState(0)
  const [showTemplateManager, setShowTemplateManager] = useState(false)
  const [showCommentModal, setShowCommentModal] = useState(false)
  const [selectedTask, setSelectedTask] = useState<Task | null>(null)
  const [comment, setComment] = useState('')
  const [isUpdatingTask, setIsUpdatingTask] = useState(false)
  const [isAddingComment, setIsAddingComment] = useState(false)
  const [showCancelConfirmation, setShowCancelConfirmation] = useState(false)
  const [taskToCancel, setTaskToCancel] = useState<Task | null>(null)
  const [showTaskDetail, setShowTaskDetail] = useState(false)
  const [selectedTaskDetail, setSelectedTaskDetail] = useState<TaskWithDetails | null>(null)
  const [isLoadingTaskDetail, setIsLoadingTaskDetail] = useState(false)

  useEffect(() => {
    if (!loading && !user) {
      router.push('/login')
    }
  }, [user, loading, router])

  useEffect(() => {
    if (user) {
      fetchTasks()
    }
  }, [user])

  useEffect(() => {
    filterTasks()
  }, [tasks, searchTerm, selectedStatus, selectedPriority, selectedRecency, currentPage, itemsPerPage])

  const fetchTasks = async () => {
    try {
      setIsLoading(true)
      console.log('Fetching tasks for user:', user?.id)
      
      // Check current session
      const { data: { session } } = await supabase.auth.getSession()
      console.log('Current session:', session?.user?.id)
      
      // Get agent ID
      const { data: agent, error: agentError } = await supabase
        .from('agents')
        .select('id')
        .eq('user_id', user?.id || '')
        .single()

      console.log('Agent query result:', { agent, agentError })
      if (!agent) return

      setAgentId(agent.id)

      // Fetch tasks first
      const { data: tasksData } = await supabase
        .from('tasks')
        .select('*')
        .eq('assigned_to', agent.id)
        .order('created_at', { ascending: false })

      if (tasksData) {
        // Get unique client and property IDs
        const clientIds = tasksData
          .filter(task => task.related_entity_type === 'client' && task.related_entity_id)
          .map(task => task.related_entity_id!)
        const propertyIds = tasksData
          .filter(task => task.related_entity_type === 'property' && task.related_entity_id)
          .map(task => task.related_entity_id!)

        // Fetch clients and properties in parallel
        const [clientsResult, propertiesResult] = await Promise.all([
          clientIds.length > 0 
            ? supabase
                .from('clients')
                .select('id, first_name, last_name')
                .in('id', clientIds)
            : Promise.resolve({ data: [] }),
          propertyIds.length > 0 
            ? supabase
                .from('properties')
                .select('id, address, city, state')
                .in('id', propertyIds)
            : Promise.resolve({ data: [] })
        ])

        const clients = clientsResult.data || []
        const properties = propertiesResult.data || []

        // Transform the data to include client names and property addresses
        const enhancedTasks: EnhancedTask[] = tasksData.map(task => {
          const enhancedTask: EnhancedTask = { ...task }
          
          // Add client name if related to client
          if (task.related_entity_type === 'client' && task.related_entity_id) {
            const client = clients.find(c => c.id === task.related_entity_id)
            if (client) {
              enhancedTask.client_name = `${client.first_name} ${client.last_name}`
            }
          }
          
          // Add property address if related to property
          if (task.related_entity_type === 'property' && task.related_entity_id) {
            const property = properties.find(p => p.id === task.related_entity_id)
            if (property) {
              enhancedTask.property_address = `${property.address}, ${property.city}, ${property.state}`
            }
          }
          
          return enhancedTask
        })
        
        setTasks(enhancedTasks)
      }
    } catch (error) {
      console.error('Error fetching tasks:', error)
    } finally {
      setIsLoading(false)
    }
  }

  const fetchTaskDetails = async (taskId: string) => {
    try {
      setIsLoadingTaskDetail(true)
      
      // Fetch task with all related data
      const { data: taskData } = await supabase
        .from('tasks')
        .select('*')
        .eq('id', taskId)
        .single()

      if (!taskData) return

      // Fetch comments for this task
      const { data: commentsData } = await supabase
        .from('task_comments')
        .select(`
          *,
          agents (
            agent_name
          )
        `)
        .eq('task_id', taskId)
        .order('created_at', { ascending: false })

      // Fetch related client if applicable
      let clientData = null
      if (taskData.related_entity_type === 'client' && taskData.related_entity_id) {
        const { data } = await supabase
          .from('clients')
          .select('*')
          .eq('id', taskData.related_entity_id)
          .single()
        clientData = data
      }

      // Fetch related property if applicable
      let propertyData = null
      if (taskData.related_entity_type === 'property' && taskData.related_entity_id) {
        const { data } = await supabase
          .from('properties')
          .select('*')
          .eq('id', taskData.related_entity_id)
          .single()
        propertyData = data
      }

      const taskWithDetails: TaskWithDetails = {
        ...taskData,
        task_comments: commentsData || [],
        client: clientData || undefined,
        property: propertyData || undefined
      }

      setSelectedTaskDetail(taskWithDetails)
      setShowTaskDetail(true)
    } catch (error) {
      console.error('Error fetching task details:', error)
    } finally {
      setIsLoadingTaskDetail(false)
    }
  }

  const filterTasks = () => {
    let filtered = tasks

    // Search filter
    if (searchTerm) {
      filtered = filtered.filter(task =>
        task.title.toLowerCase().includes(searchTerm.toLowerCase()) ||
        task.description?.toLowerCase().includes(searchTerm.toLowerCase()) ||
        task.client_name?.toLowerCase().includes(searchTerm.toLowerCase()) ||
        task.property_address?.toLowerCase().includes(searchTerm.toLowerCase())
      )
    }

    // Status filter
    if (selectedStatus !== 'all') {
      filtered = filtered.filter(task => task.status === selectedStatus)
    }

    // Priority filter
    if (selectedPriority !== 'all') {
      filtered = filtered.filter(task => task.priority === selectedPriority)
    }

    // Recency filter
    if (selectedRecency !== 'all') {
      const now = new Date()
      const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
      
      filtered = filtered.filter(task => {
        const taskDate = task.created_at ? new Date(task.created_at) : new Date()
        const taskDay = new Date(taskDate.getFullYear(), taskDate.getMonth(), taskDate.getDate())
        
        switch (selectedRecency) {
          case 'today':
            return taskDay.getTime() === today.getTime()
          case 'week':
            const weekAgo = new Date(today.getTime() - 7 * 24 * 60 * 60 * 1000)
            return taskDay >= weekAgo
          case 'month':
            const monthAgo = new Date(today.getTime() - 30 * 24 * 60 * 60 * 1000)
            return taskDay >= monthAgo
          case 'quarter':
            const quarterAgo = new Date(today.getTime() - 90 * 24 * 60 * 60 * 1000)
            return taskDay >= quarterAgo
          default:
            return true
        }
      })
    }

    setFilteredTasks(filtered)
    
    // Apply pagination
    paginateTasks(filtered)
  }

  const paginateTasks = (filtered: EnhancedTask[]) => {
    setTotalItems(filtered.length)
    
    // Reset to page 1 if current page would be empty
    let pageToUse = currentPage
    const totalPages = Math.ceil(filtered.length / itemsPerPage)
    if (currentPage > totalPages && totalPages > 0) {
      pageToUse = 1
      setCurrentPage(1)
    }
    
    const startIndex = (pageToUse - 1) * itemsPerPage
    const endIndex = startIndex + itemsPerPage
    const paginated = filtered.slice(startIndex, endIndex)
    
    setPaginatedTasks(paginated)
  }

  const updateTaskStatus = async (taskId: string, status: string) => {
    try {
      const { error } = await supabase
        .from('tasks')
        .update({ 
          status,
          completed_at: status === 'completed' ? new Date().toISOString() : null
        })
        .eq('id', taskId)

      if (error) throw error

      // Update local state
      setTasks(prev => prev.map(task => 
        task.id === taskId 
          ? { ...task, status: status as any, completed_at: status === 'completed' ? new Date().toISOString() : null }
          : task
      ))
    } catch (error) {
      console.error('Error updating task status:', error)
      alert(t('tasks.failedToUpdateStatus'))
    }
  }

  const handleStartTask = (task: Task) => {
    setSelectedTask(task)
    setComment('')
    setIsAddingComment(false)
    setShowCommentModal(true)
  }

  const handleAddComment = (task: Task) => {
    setSelectedTask(task)
    setComment('')
    setIsAddingComment(true)
    setShowCommentModal(true)
  }

  const handleCancelTask = (task: Task) => {
    setTaskToCancel(task)
    setShowCancelConfirmation(true)
  }

  const confirmCancelTask = async () => {
    if (!taskToCancel) return
    
    await updateTaskStatus(taskToCancel.id, 'cancelled')
    setShowCancelConfirmation(false)
    setTaskToCancel(null)
  }

  const handleTaskCardClick = (task: EnhancedTask) => {
    fetchTaskDetails(task.id)
  }

  const handleStartTaskWithComment = async () => {
    if (!selectedTask || !agentId) {
      console.error('Missing required data:', { selectedTask: !!selectedTask, agentId })
      return
    }

    try {
      setIsUpdatingTask(true)
      console.log('Starting task update for:', selectedTask.id, 'with agent:', agentId)

      // Test authentication
      const { data: { user: currentUser } } = await supabase.auth.getUser()
      console.log('Current authenticated user:', currentUser?.id)

      if (isAddingComment) {
        // Just add a comment without changing status
        if (comment.trim()) {
          const { error: commentError } = await supabase
            .from('task_comments')
            .insert({
              task_id: selectedTask.id,
              agent_id: agentId,
              comment: comment.trim()
            })

          if (commentError) throw commentError
        }
      } else {
        // Update task status and add comment
        // First verify the task belongs to the current agent
        const { data: taskCheck, error: taskCheckError } = await supabase
          .from('tasks')
          .select('id, assigned_to')
          .eq('id', selectedTask.id)
          .single()
          
        console.log('Task ownership check:', { 
          taskId: selectedTask.id, 
          assignedTo: taskCheck?.assigned_to, 
          currentAgent: agentId,
          matches: taskCheck?.assigned_to === agentId,
          error: taskCheckError
        })
        
        if (taskCheckError) {
          console.error('Error checking task ownership:', taskCheckError)
          throw new Error(`Cannot verify task ownership: ${taskCheckError.message}`)
        }
        
        if (!taskCheck || taskCheck.assigned_to !== agentId) {
          throw new Error('Task is not assigned to current agent')
        }

        console.log('Updating task with data:', { 
          taskId: selectedTask.id, 
          status: 'in_progress', 
          comments: comment.trim() || null 
        })
        
        const { error: taskError, data } = await supabase
          .from('tasks')
          .update({ 
            status: 'in_progress',
            comments: comment.trim() || null
          })
          .eq('id', selectedTask.id)
          .select()

        console.log('Task update result:', { data, error: taskError })
        if (taskError) throw taskError

        // Add comment to task_comments table if comment is provided
        if (comment.trim()) {
          const { error: commentError } = await supabase
            .from('task_comments')
            .insert({
              task_id: selectedTask.id,
              agent_id: agentId,
              comment: comment.trim()
            })

          if (commentError) throw commentError
        }

        // Update local state
        setTasks(prev => prev.map(task => 
          task.id === selectedTask.id 
            ? { ...task, status: 'in_progress', comments: comment.trim() || null }
            : task
        ))
      }

      // Close modal
      setShowCommentModal(false)
      setSelectedTask(null)
      setComment('')
    } catch (error) {
      console.error('Error updating task:', error)
      console.error('Error type:', typeof error)
      console.error('Error details:', JSON.stringify(error, null, 2))
      console.error('Error keys:', Object.keys(error || {}))
      
      let errorMessage = t('tasks.unknownError')
      if (error && typeof error === 'object') {
        if ('message' in error) {
          errorMessage = String(error.message)
        } else if ('error' in error) {
          errorMessage = String(error.error)
        } else if ('details' in error) {
          errorMessage = String(error.details)
        } else if ('hint' in error) {
          errorMessage = String(error.hint)
        }
      }

      alert(`${t('tasks.failedToUpdateTask')}: ${errorMessage}`)
    } finally {
      setIsUpdatingTask(false)
    }
  }

  const handleCloseCommentModal = () => {
    setShowCommentModal(false)
    setSelectedTask(null)
    setComment('')
  }

  const handleCloseTaskDetail = () => {
    setShowTaskDetail(false)
    setSelectedTaskDetail(null)
  }

  const handlePageChange = (page: number) => {
    setCurrentPage(page)
  }

  const handleItemsPerPageChange = (newItemsPerPage: number) => {
    setItemsPerPage(newItemsPerPage)
    setCurrentPage(1) // Reset to first page when changing items per page
  }

  const resetFiltersAndPagination = () => {
    setSearchTerm('')
    setSelectedStatus('all')
    setSelectedPriority('all')
    setSelectedRecency('all')
    setCurrentPage(1)
  }

  const getPriorityColor = (priority: string) => {
    switch (priority) {
      case 'urgent': return 'bg-red-100 text-red-800 border-red-200'
      case 'high': return 'bg-orange-100 text-orange-800 border-orange-200'
      case 'medium': return 'bg-yellow-100 text-yellow-800 border-yellow-200'
      case 'low': return 'bg-green-100 text-green-800 border-green-200'
      default: return 'bg-gray-100 text-gray-800 border-gray-200'
    }
  }

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'completed': return 'bg-green-100 text-green-800'
      case 'in_progress': return 'bg-blue-100 text-blue-800'
      case 'pending': return 'bg-yellow-100 text-yellow-800'
      case 'cancelled': return 'bg-red-100 text-red-800'
      default: return 'bg-gray-100 text-gray-800'
    }
  }

  const getTaskTypeIcon = (taskType: string) => {
    switch (taskType) {
      case 'follow_up': return <Clock className="w-4 h-4 text-gray-800" />
      case 'showing': return <Home className="w-4 h-4 text-gray-800" />
      case 'document': return <FileText className="w-4 h-4 text-gray-800" />
      case 'administrative': return <Settings className="w-4 h-4 text-gray-800" />
      default: return <CheckSquare className="w-4 h-4 text-gray-800" />
    }
  }

  const formatDate = (dateString: string | null) => {
    if (!dateString) return t('tasks.noDueDate')
    const date = new Date(dateString)
    const now = new Date()
    const diffTime = date.getTime() - now.getTime()
    const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24))

    if (diffDays === 0) return t('tasks.dueToday')
    if (diffDays === 1) return t('tasks.dueTomorrow')
    if (diffDays === -1) return t('tasks.dueYesterday')
    if (diffDays < 0) return t('tasks.overdueBy').replace('{days}', String(Math.abs(diffDays)))
    return t('tasks.dueIn').replace('{days}', String(diffDays))
  }

  const formatCreatedDate = (dateString: string) => {
    const date = new Date(dateString)
    const now = new Date()
    const diffTime = now.getTime() - date.getTime()
    const diffDays = Math.floor(diffTime / (1000 * 60 * 60 * 24))

    if (diffDays === 0) return t('tasks.today')
    if (diffDays === 1) return t('tasks.yesterday')
    if (diffDays < 7) return t('tasks.daysAgo').replace('{days}', String(diffDays))
    if (diffDays < 30) return t('tasks.weeksAgo').replace('{weeks}', String(Math.floor(diffDays / 7)))
    if (diffDays < 365) return t('tasks.monthsAgo').replace('{months}', String(Math.floor(diffDays / 30)))
    return t('tasks.yearsAgo').replace('{years}', String(Math.floor(diffDays / 365)))
  }

  const isOverdue = (dueDate: string | null) => {
    if (!dueDate) return false
    return new Date(dueDate) < new Date()
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

  return (
    <div className="min-h-screen bg-gray-50">
      <MainNavigation title={t('nav.tasks')} />

      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {/* Header */}
        <div className="mb-6 flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
          <div>
            <h1 className="text-2xl font-bold text-gray-900">{t('nav.tasks')}</h1>
            <p className="text-gray-800 font-medium">{t('tasks.manageWorkflows')}</p>
          </div>
          <div className="flex space-x-3">
            <Button
              variant="outline"
              onClick={() => setShowTemplateManager(!showTemplateManager)}
            >
              <Settings className="w-4 h-4 mr-2 text-gray-800" />
              {showTemplateManager ? t('tasks.hideTemplates') : t('tasks.manageTemplates')}
            </Button>
          </div>
        </div>

        {/* Template Manager */}
        {showTemplateManager && (
          <div className="mb-8">
            <TaskTemplateManager agentId={agentId} />
          </div>
        )}

        {/* Search and Filters */}
        <div className="mb-6 space-y-4">
          <div className="flex flex-col sm:flex-row gap-4">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-700 w-4 h-4" />
              <Input
                placeholder={t('tasks.searchTasksClientsProperties')}
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                className="pl-10"
              />
            </div>
            <div className="flex gap-2">
              <select
                value={selectedStatus}
                onChange={(e) => setSelectedStatus(e.target.value)}
                className="px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 text-gray-900 font-medium"
              >
                <option value="all">{t('tasks.allStatus')}</option>
                <option value="pending">{t('tasks.pending')}</option>
                <option value="in_progress">{t('tasks.inProgress')}</option>
                <option value="completed">{t('tasks.completed')}</option>
                <option value="cancelled">{t('tasks.cancelled')}</option>
              </select>
              <select
                value={selectedPriority}
                onChange={(e) => setSelectedPriority(e.target.value)}
                className="px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 text-gray-900 font-medium"
              >
                <option value="all">{t('tasks.allPriority')}</option>
                <option value="urgent">{t('tasks.urgent')}</option>
                <option value="high">{t('tasks.high')}</option>
                <option value="medium">{t('tasks.medium')}</option>
                <option value="low">{t('tasks.low')}</option>
              </select>
              <select
                value={selectedRecency}
                onChange={(e) => setSelectedRecency(e.target.value)}
                className="px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 text-gray-900 font-medium"
              >
                <option value="all">{t('tasks.allTime')}</option>
                <option value="today">{t('tasks.today')}</option>
                <option value="week">{t('tasks.lastWeek')}</option>
                <option value="month">{t('tasks.lastMonth')}</option>
                <option value="quarter">{t('tasks.lastQuarter')}</option>
              </select>
            </div>
          </div>
        </div>

        {/* Results Summary */}
        <div className="mb-6 flex items-center justify-between">
          <p className="text-sm text-gray-900 font-medium">
            {t('tasks.showingOfTasks').replace('{shown}', String(paginatedTasks.length)).replace('{filtered}', String(filteredTasks.length))}
            {(searchTerm || selectedStatus !== 'all' || selectedPriority !== 'all' || selectedRecency !== 'all') && (
              <span className="text-blue-600 ml-2">
                ({t('tasks.filteredFromTotal').replace('{total}', String(tasks.length))})
              </span>
            )}
          </p>
          {(searchTerm || selectedStatus !== 'all' || selectedPriority !== 'all' || selectedRecency !== 'all') && (
            <button
              onClick={() => {
                setSearchTerm('')
                setSelectedStatus('all')
                setSelectedPriority('all')
                setSelectedRecency('all')
              }}
              className="text-sm text-blue-600 hover:text-blue-800"
            >
              {t('common.clearFilters')}
            </button>
          )}
        </div>

        {/* Tasks List */}
        {isLoading ? (
          <div className="space-y-4">
            {[...Array(5)].map((_, i) => (
              <Card key={`tasks-loading-skeleton-${i}`} className="animate-pulse">
                <CardContent className="p-6">
                  <div className="h-4 bg-gray-200 rounded w-3/4 mb-2"></div>
                  <div className="h-4 bg-gray-200 rounded w-1/2"></div>
                </CardContent>
              </Card>
            ))}
          </div>
        ) : filteredTasks.length === 0 ? (
          <Card>
            <CardContent className="p-12 text-center">
              <CheckSquare className="w-16 h-16 text-gray-700 mx-auto mb-4" />
              <h3 className="text-lg font-medium text-gray-900 mb-2">{t('tasks.noTasksFound')}</h3>
              <p className="text-gray-800 mb-4">
                {searchTerm || selectedStatus !== 'all' || selectedPriority !== 'all' || selectedRecency !== 'all'
                  ? t('tasks.tryAdjustingFilters')
                  : t('tasks.useTemplatesToCreate')
                }
              </p>
              <Button
                onClick={() => setShowTemplateManager(true)}
                className="bg-blue-600 hover:bg-blue-700 text-white"
              >
                <Settings className="w-4 h-4 mr-2 text-white" />
                {t('tasks.manageTemplates')}
              </Button>
            </CardContent>
          </Card>
        ) : (
          <>
            <div className="space-y-4">
              {paginatedTasks.map((task) => (
              <Card 
                key={task.id} 
                className={`border-l-4 cursor-pointer hover:shadow-md transition-shadow ${isOverdue(task.due_date) && task.status !== 'completed' ? 'border-l-red-500' : 'border-l-blue-500'}`}
                onClick={() => handleTaskCardClick(task)}
              >
                <CardContent className="p-6">
                  <div className="flex items-start justify-between">
                    <div className="flex-1">
                      <div className="flex items-center space-x-3 mb-2">
                        {getTaskTypeIcon(task.task_type || '')}
                        <h3 className="font-semibold text-gray-900">{task.title}</h3>
                        <span className={`px-2 py-1 rounded-full text-xs font-medium ${getPriorityColor(task.priority || '')}`}>
                          {task.priority}
                        </span>
                        <span className={`px-2 py-1 rounded-full text-xs font-medium ${getStatusColor(task.status || '')}`}>
                          {task.status}
                        </span>
                      </div>
                      
                      {task.description && (
                        <p className="text-gray-800 mb-3">{task.description}</p>
                      )}
                      
                      {/* Client and Property Information */}
                      <div className="flex flex-wrap items-center gap-4 text-sm text-gray-800 font-medium mb-3">
                        <div className="flex items-center space-x-1">
                          <Calendar className="w-4 h-4 text-gray-800" />
                          <span className={isOverdue(task.due_date) && task.status !== 'completed' ? 'text-red-600 font-medium' : ''}>
                            {formatDate(task.due_date)}
                          </span>
                        </div>
                        <div className="flex items-center space-x-1">
                          <CalendarDays className="w-4 h-4 text-gray-600" />
                          <span className="text-gray-600">{t('tasks.created')}: {task.created_at ? formatCreatedDate(task.created_at) : t('tasks.unknown')}</span>
                        </div>
                        {task.task_type && (
                          <div className="flex items-center space-x-1">
                            <span>{t('tasks.type')}: {task.task_type}</span>
                          </div>
                        )}
                        {task.client_name && (
                          <div className="flex items-center space-x-1">
                            <User className="w-4 h-4 text-gray-800" />
                            <span>{t('tasks.client')}: {task.client_name}</span>
                          </div>
                        )}
                        {task.property_address && (
                          <div className="flex items-center space-x-1">
                            <Home className="w-4 h-4 text-gray-800" />
                            <span>{t('tasks.property')}: {task.property_address}</span>
                          </div>
                        )}
                      </div>

                      {task.comments && (
                        <div className="mt-3 p-3 bg-gray-50 rounded-lg">
                          <div className="flex items-center space-x-2 mb-2">
                            <FileText className="w-4 h-4 text-gray-600" />
                            <span className="text-sm font-medium text-gray-900">{t('tasks.comment')}:</span>
                          </div>
                          <p className="text-sm text-gray-800">{task.comments}</p>
                        </div>
                      )}
                    </div>
                    
                    <div className="flex items-center space-x-2" onClick={(e) => e.stopPropagation()}>
                      {task.status === 'pending' && (
                        <Button
                          size="sm"
                          onClick={() => handleStartTask(task)}
                          className="bg-blue-600 hover:bg-blue-700 text-white"
                        >
                          {t('tasks.start')}
                        </Button>
                      )}
                      {task.status === 'in_progress' && (
                        <Button
                          size="sm"
                          onClick={() => updateTaskStatus(task.id, 'completed')}
                          className="bg-green-600 hover:bg-green-700 text-white"
                        >
                          {t('tasks.complete')}
                        </Button>
                      )}
                      {task.status !== 'completed' && task.status !== 'cancelled' && (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => handleCancelTask(task)}
                        >
                          {t('tasks.cancel')}
                        </Button>
                      )}
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => handleAddComment(task)}
                        className="text-blue-600 hover:text-blue-700 border-blue-200 hover:border-blue-300"
                      >
                        <FileText className="w-4 h-4 mr-1" />
                        {t('tasks.addComment')}
                      </Button>
                    </div>
                  </div>
                </CardContent>
              </Card>
              ))}
            </div>
            
            {/* Pagination */}
            {filteredTasks.length > 0 && (
              <div className="mt-8">
                <Pagination
                  currentPage={currentPage}
                  totalItems={totalItems}
                  itemsPerPage={itemsPerPage}
                  onPageChange={handlePageChange}
                  onItemsPerPageChange={handleItemsPerPageChange}
                  className="border-t pt-6"
                />
              </div>
            )}
          </>
        )}
      </main>

      {/* Comment Modal */}
      {showCommentModal && selectedTask && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg p-6 w-full max-w-md mx-4">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">
              {isAddingComment ? t('tasks.addCommentToTask') : t('tasks.startTask')}
            </h3>

            <div className="mb-4">
              <label className="block text-sm font-medium text-gray-900 mb-2">
                {t('tasks.addCommentOptional')}
              </label>
              <textarea
                value={comment}
                onChange={(e) => setComment(e.target.value)}
                placeholder={t('tasks.commentPlaceholder')}
                className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
                rows={4}
              />
            </div>

            <div className="flex justify-end space-x-3">
              <Button
                variant="outline"
                onClick={handleCloseCommentModal}
                disabled={isUpdatingTask}
              >
                {t('common.cancel')}
              </Button>
              <Button
                onClick={handleStartTaskWithComment}
                disabled={isUpdatingTask}
                className="bg-blue-600 hover:bg-blue-700 text-white"
              >
                {isUpdatingTask ? (isAddingComment ? t('tasks.adding') : t('tasks.starting')) : (isAddingComment ? t('tasks.addComment') : t('tasks.startTask'))}
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Cancel Confirmation Modal */}
      {showCancelConfirmation && taskToCancel && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg p-6 w-full max-w-md mx-4">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">
              {t('tasks.cancelTask')}
            </h3>

            <p className="text-gray-800 mb-6">
              {t('tasks.confirmCancelTask').replace('{title}', taskToCancel.title)}
            </p>

            <div className="flex justify-end space-x-3">
              <Button
                variant="outline"
                onClick={() => setShowCancelConfirmation(false)}
              >
                {t('tasks.keepTask')}
              </Button>
              <Button
                onClick={confirmCancelTask}
                className="bg-red-600 hover:bg-red-700 text-white"
              >
                {t('tasks.cancelTask')}
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Task Detail Modal */}
      {showTaskDetail && selectedTaskDetail && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg w-full max-w-4xl mx-4 max-h-[90vh] overflow-y-auto">
            <div className="p-6">
              <div className="flex justify-between items-start mb-6">
                <div>
                  <h2 className="text-2xl font-bold text-gray-900 mb-2">{selectedTaskDetail.title}</h2>
                  <div className="flex items-center space-x-3">
                    <span className={`px-3 py-1 rounded-full text-sm font-medium ${getPriorityColor(selectedTaskDetail.priority || '')}`}>
                      {selectedTaskDetail.priority}
                    </span>
                    <span className={`px-3 py-1 rounded-full text-sm font-medium ${getStatusColor(selectedTaskDetail.status || '')}`}>
                      {selectedTaskDetail.status}
                    </span>
                  </div>
                </div>
                <Button
                  variant="outline"
                  onClick={handleCloseTaskDetail}
                  className="p-2"
                >
                  <X className="w-4 h-4" />
                </Button>
              </div>

              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                {/* Task Information */}
                <div className="space-y-6">
                  <div>
                    <h3 className="text-lg font-semibold text-gray-900 mb-3">{t('tasks.taskDetails')}</h3>
                    <div className="space-y-3">
                      {selectedTaskDetail.description && (
                        <div>
                          <label className="block text-sm font-medium text-gray-700 mb-1">{t('tasks.description')}</label>
                          <p className="text-gray-800">{selectedTaskDetail.description}</p>
                        </div>
                      )}
                      <div>
                        <label className="block text-sm font-medium text-gray-700 mb-1">{t('tasks.dueDate')}</label>
                        <p className="text-gray-800">{formatDate(selectedTaskDetail.due_date)}</p>
                      </div>
                      <div>
                        <label className="block text-sm font-medium text-gray-700 mb-1">{t('tasks.type')}</label>
                        <p className="text-gray-800">{selectedTaskDetail.task_type || t('tasks.notSpecified')}</p>
                      </div>
                      <div>
                        <label className="block text-sm font-medium text-gray-700 mb-1">{t('tasks.created')}</label>
                        <p className="text-gray-800">{selectedTaskDetail.created_at ? new Date(selectedTaskDetail.created_at).toLocaleDateString() : t('tasks.unknown')}</p>
                      </div>
                    </div>
                  </div>

                  {/* Related Client */}
                  {selectedTaskDetail.client && (
                    <div>
                      <h3 className="text-lg font-semibold text-gray-900 mb-3">{t('tasks.relatedClient')}</h3>
                      <div className="bg-blue-50 p-4 rounded-lg">
                        <div className="flex items-center space-x-2 mb-2">
                          <User className="w-5 h-5 text-blue-600" />
                          <h4 className="font-medium text-gray-900">
                            {selectedTaskDetail.client.first_name} {selectedTaskDetail.client.last_name}
                          </h4>
                        </div>
                        <div className="space-y-2 text-sm">
                          <div className="flex items-center space-x-2">
                            <Mail className="w-4 h-4 text-gray-600" />
                            <span className="text-gray-800">{selectedTaskDetail.client.email}</span>
                          </div>
                          {selectedTaskDetail.client.phone && (
                            <div className="flex items-center space-x-2">
                              <Phone className="w-4 h-4 text-gray-600" />
                              <span className="text-gray-800">{selectedTaskDetail.client.phone}</span>
                            </div>
                          )}
                          {selectedTaskDetail.client.client_type && (
                            <div className="flex items-center space-x-2">
                              <span className="text-gray-600">{t('tasks.type')}:</span>
                              <span className="text-gray-800 capitalize">{selectedTaskDetail.client.client_type}</span>
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  )}

                  {/* Related Property */}
                  {selectedTaskDetail.property && (
                    <div>
                      <h3 className="text-lg font-semibold text-gray-900 mb-3">{t('tasks.relatedProperty')}</h3>
                      <div className="bg-green-50 p-4 rounded-lg">
                        <div className="flex items-center space-x-2 mb-2">
                          <Home className="w-5 h-5 text-green-600" />
                          <h4 className="font-medium text-gray-900">
                            {selectedTaskDetail.property.address}
                          </h4>
                        </div>
                        <div className="space-y-2 text-sm">
                          <div className="flex items-center space-x-2">
                            <MapPin className="w-4 h-4 text-gray-600" />
                            <span className="text-gray-800">
                              {selectedTaskDetail.property.city}, {selectedTaskDetail.property.state} {selectedTaskDetail.property.zip_code}
                            </span>
                          </div>
                          {selectedTaskDetail.property.price && (
                            <div className="flex items-center space-x-2">
                              <DollarSign className="w-4 h-4 text-gray-600" />
                              <span className="text-gray-800">
                                {selectedTaskDetail.property.price.toLocaleString()}€
                              </span>
                            </div>
                          )}
                          {selectedTaskDetail.property.property_type && (
                            <div className="flex items-center space-x-2">
                              <span className="text-gray-600">{t('tasks.type')}:</span>
                              <span className="text-gray-800 capitalize">{selectedTaskDetail.property.property_type.replace('_', ' ')}</span>
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  )}
                </div>

                {/* Comments Section */}
                <div>
                  <h3 className="text-lg font-semibold text-gray-900 mb-3">{t('tasks.comments')}</h3>
                  <div className="space-y-4 max-h-96 overflow-y-auto">
                    {selectedTaskDetail.task_comments.length === 0 ? (
                      <p className="text-gray-500 text-center py-8">{t('tasks.noCommentsYet')}</p>
                    ) : (
                      selectedTaskDetail.task_comments.map((comment: any) => (
                        <div key={comment.id} className="bg-gray-50 p-4 rounded-lg">
                          <div className="flex items-center space-x-2 mb-2">
                            <MessageCircle className="w-4 h-4 text-gray-600" />
                            <span className="text-sm font-medium text-gray-900">
                              {comment.agents?.agent_name || t('tasks.agent')}
                            </span>
                            <span className="text-xs text-gray-500">
                              {new Date(comment.created_at).toLocaleDateString()} at {new Date(comment.created_at).toLocaleTimeString()}
                            </span>
                          </div>
                          <p className="text-gray-800">{comment.comment}</p>
                        </div>
                      ))
                    )}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
} 