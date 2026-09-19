'use client'

import { useState, useEffect } from 'react'
import { useAuth } from '@/contexts/AuthContext'
import { useLanguage } from '@/contexts/LanguageContext'
import { usePropertyStore } from '@/stores/usePropertyStore'
import { useAutoSave } from '@/hooks/useAutoSave'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { supabase } from '@/lib/api'
import { Database } from '@/types/database'
import { showToast } from '@/lib/toast'

type PropertyInsert = Database['public']['Tables']['properties']['Insert']
type Property = Database['public']['Tables']['properties']['Row']
type Agent = Database['public']['Tables']['agents']['Row']

interface PropertyFormProps {
  onClose: () => void
  onSuccess: () => void
  initialData?: Property | null
}

// Function to generate the next property ID
const generateNextPropertyId = async (): Promise<string> => {
  try {
    const { data, error } = await supabase
      .from('properties')
      .select('property_id')
      .order('property_id', { ascending: false })
      .limit(1)

    if (error) throw error

    if (data && data.length > 0) {
      const lastId = data[0].property_id
      // Extract the number from the last ID (e.g., "PROP-006" -> 6)
      const match = lastId.match(/PROP-(\d+)/)
      if (match) {
        const lastNumber = parseInt(match[1])
        const nextNumber = lastNumber + 1
        return `PROP-${nextNumber.toString().padStart(3, '0')}`
      }
    }
    
    // If no properties exist or pattern doesn't match, start with PROP-001
    return 'PROP-001'
  } catch (error) {
    console.error('Error generating property ID:', error)
    // Fallback: try to generate a unique ID with timestamp
    const timestamp = Date.now().toString().slice(-6)
    return `PROP-${timestamp}`
  }
}

// Function to validate if a property ID is available
const validatePropertyId = async (propertyId: string): Promise<boolean> => {
  try {
    const { data, error } = await supabase
      .from('properties')
      .select('id')
      .eq('property_id', propertyId)
      .limit(1)

    if (error) throw error
    return data.length === 0 // true if no existing property with this ID
  } catch (error) {
    console.error('Error validating property ID:', error)
    return false
  }
}

function Field({
  label,
  name,
  value,
  onChange,
  type = 'text',
  step,
}: {
  label: string
  name: string
  value: string
  onChange: (e: React.ChangeEvent<HTMLInputElement>) => void
  type?: string
  step?: string
}) {
  return (
    <div>
      <label className="block text-sm font-medium text-gray-700 mb-1">{label}</label>
      <input
        name={name}
        value={value}
        onChange={onChange}
        type={type}
        step={step}
        className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
      />
    </div>
  )
}

export default function PropertyForm({ onClose, onSuccess, initialData }: PropertyFormProps) {
  const { user } = useAuth()
  const { t } = useLanguage()
  const { createProperty, updateProperty } = usePropertyStore()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [agents, setAgents] = useState<Agent[]>([])
  const [currentAgentId, setCurrentAgentId] = useState<string | null>(null)
  const [generatingId, setGeneratingId] = useState(false)
  const isEditing = !!initialData

  const [formData, setFormData] = useState({
    property_id: initialData?.property_id || '',
    address: initialData?.address || '',
    city: initialData?.city || '',
    state: initialData?.state || '',
    zip_code: initialData?.zip_code || '',
    price: initialData?.price?.toString() || '',
    bedrooms: initialData?.bedrooms?.toString() || '',
    bathrooms: initialData?.bathrooms?.toString() || '',
    square_feet: initialData?.square_feet?.toString() || '',
    lot_size: initialData?.lot_size?.toString() || '',
    year_built: initialData?.year_built?.toString() || '',
    property_type: initialData?.property_type || 'single_family',
    listing_status: initialData?.listing_status || 'active',
    mls_number: initialData?.mls_number || '',
    description: initialData?.description || '',
    assigned_agent_id: initialData?.assigned_agent_id || '',
    // Public marketing site fields
    slug: (initialData as any)?.slug || '',
    plot_size: (initialData as any)?.plot_size?.toString() || '',
    terrain_description: (initialData as any)?.terrain_description || '',
    floor_count: (initialData as any)?.floor_count?.toString() || '',
    house_history: (initialData as any)?.house_history || '',
    concept_description: (initialData as any)?.concept_description || '',
    construction_walls: (initialData as any)?.construction_details?.walls || '',
    construction_insulation: (initialData as any)?.construction_details?.insulation || '',
    construction_roof: (initialData as any)?.construction_details?.roof || '',
    construction_windows: (initialData as any)?.construction_details?.windows || '',
    engineering_heating: (initialData as any)?.engineering_details?.heating || '',
    engineering_ventilation: (initialData as any)?.engineering_details?.ventilation || '',
    engineering_water: (initialData as any)?.engineering_details?.water_supply || '',
    engineering_sewage: (initialData as any)?.engineering_details?.sewage || '',
    video_url: (initialData as any)?.video_url || '',
    map_lat: (initialData as any)?.map_lat?.toString() || '',
    map_lng: (initialData as any)?.map_lng?.toString() || '',
    featured: !!(initialData as any)?.featured,
    public_listing: !!(initialData as any)?.public_listing,
    // Brochure fields
    property_name: (initialData as any)?.property_name || '',
    room_count: (initialData as any)?.room_count?.toString() || '',
    parking_spaces: (initialData as any)?.parking_spaces?.toString() || '',
    condition_rating: (initialData as any)?.condition_rating || '',
    dpe_rating: (initialData as any)?.dpe_rating || '',
    terrace_description: (initialData as any)?.terrace_description || '',
    availability_period: (initialData as any)?.availability_period || '',
    reference_number: (initialData as any)?.reference_number || '',
    rental_type: (initialData as any)?.rental_type || '',
    listing_type_label: (initialData as any)?.listing_type_label || '',
    view_description: (initialData as any)?.view_description || '',
    district: (initialData as any)?.district || '',
    distance_to_monaco: (initialData as any)?.distance_to_monaco || '',
  })

  const [roomLayout, setRoomLayout] = useState<{ name: string; description: string }[]>(
    (initialData as any)?.room_layout || []
  )
  const [galleryUrls, setGalleryUrls] = useState<string>(
    ((initialData as any)?.gallery_urls || []).join('\n')
  )
  const [floorPlanUrls, setFloorPlanUrls] = useState<string>(
    ((initialData as any)?.floor_plan_urls || []).join('\n')
  )

  // Auto-save functionality
  const autoSaveKey = isEditing ? `property_edit_${initialData?.id}` : 'property_new'
  const { clearSavedData, hasSavedData } = useAutoSave({
    key: autoSaveKey,
    data: formData,
    enabled: !isEditing, // Only auto-save for new properties
    onRestore: (savedData) => {
      if (!isEditing && savedData) {
        setFormData(savedData)
        showToast.success(t('clients.draftRestored'))
      }
    },
  })

  useEffect(() => {
    if (user) {
      fetchAgents()
      getCurrentAgentId()
      
      // Generate property ID for new properties
      if (!isEditing && !formData.property_id) {
        generatePropertyId()
      }
    }
  }, [user])

  const generatePropertyId = async () => {
    setGeneratingId(true)
    setError('')
    try {
      let attempts = 0
      let nextId = ''
      let isAvailable = false
      
      // Try up to 3 times to generate a unique ID
      while (!isAvailable && attempts < 3) {
        nextId = await generateNextPropertyId()
        isAvailable = await validatePropertyId(nextId)
        
        if (!isAvailable) {
          attempts++
          // If the sequential ID is taken, try with a timestamp suffix
          if (attempts === 2) {
            const timestamp = Date.now().toString().slice(-4)
            nextId = `PROP-${nextId.split('-')[1]}-${timestamp}`
            isAvailable = await validatePropertyId(nextId)
          }
        }
      }
      
      if (isAvailable) {
        setFormData(prev => ({ ...prev, property_id: nextId }))
      } else {
        throw new Error('Unable to generate unique property ID')
      }
    } catch (error) {
      console.error('Error generating property ID:', error)
      setError('Failed to generate property ID. Please try again.')
    } finally {
      setGeneratingId(false)
    }
  }

  const fetchAgents = async () => {
    try {
      const { data, error } = await supabase
        .from('agents')
        .select('*')
        .eq('status', 'active')
        .order('agent_name')

      if (error) throw error
      setAgents(data || [])
    } catch (error) {
      console.error('Error fetching agents:', error)
    }
  }

  const getCurrentAgentId = async () => {
    try {
      const { data } = await supabase
        .from('agents')
        .select('id')
        .eq('user_id', user?.id)
        .single()

      if (data) {
        setCurrentAgentId(data.id)
        if (!initialData) {
          setFormData(prev => ({ ...prev, assigned_agent_id: data.id }))
        }
      }
    } catch (error) {
      console.error('Error getting current agent:', error)
    }
  }

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => {
    const { name, value } = e.target
    setFormData(prev => ({ ...prev, [name]: value }))
  }

  const handleCheckboxChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const { name, checked } = e.target
    setFormData(prev => ({ ...prev, [name]: checked }))
  }

  const slugify = (s: string) =>
    s
      .toLowerCase()
      .normalize('NFKD')
      .replace(/[̀-ͯ]/g, '')
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/(^-|-$)/g, '')

  const generateSlug = () => {
    const base = [formData.address, formData.city].filter(Boolean).join(' ')
    setFormData(prev => ({ ...prev, slug: slugify(base) || `property-${Date.now()}` }))
  }

  const addRoomRow = () => setRoomLayout(prev => [...prev, { name: '', description: '' }])
  const updateRoomRow = (i: number, field: 'name' | 'description', value: string) =>
    setRoomLayout(prev => prev.map((r, idx) => (idx === i ? { ...r, [field]: value } : r)))
  const removeRoomRow = (i: number) => setRoomLayout(prev => prev.filter((_, idx) => idx !== i))

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    setError('')

    try {
      // Debug: Check authentication state
      const { data: { session } } = await supabase.auth.getSession()
      console.log('Current session:', session?.user?.id)
      console.log('Context user:', user?.id)
      
      if (!currentAgentId) {
        throw new Error('Unable to determine current agent')
      }

      if (!formData.property_id) {
        throw new Error('Property ID is required')
      }

      console.log('Creating property with:', {
        currentAgentId,
        user_id: user?.id,
        session_user_id: session?.user?.id,
        property_id: formData.property_id
      })

      // Debug: Verify agent exists and belongs to user
      const { data: agentVerification, error: agentError } = await supabase
        .from('agents')
        .select('id, user_id, agent_name')
        .eq('id', currentAgentId)
        .single()

      console.log('Agent verification:', { agentVerification, agentError })
      
      if (agentError || !agentVerification) {
        throw new Error('Agent verification failed')
      }

      if (agentVerification.user_id !== user?.id) {
        throw new Error('Agent does not belong to current user')
      }

      const propertyData: PropertyInsert = {
        property_id: formData.property_id,
        address: formData.address,
        city: formData.city,
        state: formData.state,
        zip_code: formData.zip_code,
        price: formData.price ? parseFloat(formData.price) : null,
        bedrooms: formData.bedrooms ? parseInt(formData.bedrooms) : null,
        bathrooms: formData.bathrooms ? parseFloat(formData.bathrooms) : null,
        square_feet: formData.square_feet ? parseInt(formData.square_feet) : null,
        lot_size: formData.lot_size ? parseFloat(formData.lot_size) : null,
        year_built: formData.year_built ? parseInt(formData.year_built) : null,
        property_type: formData.property_type as any,
        listing_status: formData.listing_status as any,
        mls_number: formData.mls_number || null,
        description: formData.description || null,
        assigned_agent_id: formData.assigned_agent_id || currentAgentId,
        created_by: currentAgentId,
        listing_date: isEditing ? initialData.listing_date : new Date().toISOString(),
        // Public marketing site fields
        slug: formData.slug || null,
        plot_size: formData.plot_size ? parseFloat(formData.plot_size) : null,
        terrain_description: formData.terrain_description || null,
        floor_count: formData.floor_count ? parseInt(formData.floor_count) : null,
        house_history: formData.house_history || null,
        concept_description: formData.concept_description || null,
        construction_details: {
          walls: formData.construction_walls || undefined,
          insulation: formData.construction_insulation || undefined,
          roof: formData.construction_roof || undefined,
          windows: formData.construction_windows || undefined,
        },
        engineering_details: {
          heating: formData.engineering_heating || undefined,
          ventilation: formData.engineering_ventilation || undefined,
          water_supply: formData.engineering_water || undefined,
          sewage: formData.engineering_sewage || undefined,
        },
        room_layout: roomLayout.filter(r => r.name),
        gallery_urls: galleryUrls.split('\n').map(s => s.trim()).filter(Boolean),
        floor_plan_urls: floorPlanUrls.split('\n').map(s => s.trim()).filter(Boolean),
        video_url: formData.video_url || null,
        map_lat: formData.map_lat ? parseFloat(formData.map_lat) : null,
        map_lng: formData.map_lng ? parseFloat(formData.map_lng) : null,
        featured: formData.featured ? 1 : 0,
        public_listing: formData.public_listing ? 1 : 0,
        // Brochure fields
        property_name: formData.property_name || null,
        room_count: formData.room_count ? parseInt(formData.room_count) : null,
        parking_spaces: formData.parking_spaces ? parseInt(formData.parking_spaces) : null,
        condition_rating: formData.condition_rating || null,
        dpe_rating: formData.dpe_rating || null,
        terrace_description: formData.terrace_description || null,
        availability_period: formData.availability_period || null,
        reference_number: formData.reference_number || null,
        rental_type: formData.rental_type || null,
        listing_type_label: formData.listing_type_label || null,
        view_description: formData.view_description || null,
        district: formData.district || null,
        distance_to_monaco: formData.distance_to_monaco || null,
      } as any

      let result
      if (isEditing) {
        result = await updateProperty(initialData.id, propertyData)
      } else {
        result = await createProperty(propertyData)
      }
      
      if (result) {
        clearSavedData() // Clear auto-saved data on successful submission
        onSuccess()
        onClose()
      } else {
        setError(`Failed to ${isEditing ? 'update' : 'create'} property`)
      }
    } catch (error) {
      console.error('Property creation/update error:', error)
      
      // Provide more specific error messages
      let errorMessage = 'An error occurred'
      
      if (error instanceof Error) {
        errorMessage = error.message
        
        // Check for common RLS policy errors
        if (error.message.includes('new row violates row-level security policy')) {
          errorMessage = 'You do not have permission to create properties. Please contact your administrator.'
        } else if (error.message.includes('duplicate key value')) {
          errorMessage = 'A property with this ID already exists. Please try generating a new ID.'
        } else if (error.message.includes('violates foreign key constraint')) {
          errorMessage = 'Invalid agent assignment. Please select a valid agent.'
        }
      }
      
      setError(errorMessage)
    } finally {
      setLoading(false)
    }
  }

  return (
    <Card className="w-full">
      <CardHeader>
        <CardTitle>{isEditing ? t('properties.editProperty') : t('properties.addNewProperty')}</CardTitle>
      </CardHeader>
      <CardContent>
        {error && (
          <div className="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded mb-4">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <div className="flex items-center gap-2">
                <Input
                  label={t('properties.propertyId')}
                  name="property_id"
                  value={formData.property_id}
                  onChange={handleInputChange}
                  disabled={true}
                  required
                  className="bg-gray-50 flex-1"
                />
                {!isEditing && (
                  <Button
                    type="button"
                    onClick={generatePropertyId}
                    disabled={generatingId}
                    className="mt-6 px-3 py-2 text-sm bg-blue-100 text-blue-700 hover:bg-blue-200 border border-blue-300"
                  >
                    {generatingId ? '...' : '↻'}
                  </Button>
                )}
              </div>
              {generatingId && (
                <p className="text-sm text-blue-600 mt-1">{t('properties.generatingPropertyId')}</p>
              )}
              {!isEditing && !generatingId && (
                <p className="text-sm text-gray-500 mt-1">{t('properties.autoGeneratedId')}</p>
              )}
              {isEditing && (
                <p className="text-sm text-gray-500 mt-1">{t('properties.idCannotBeChanged')}</p>
              )}
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                {t('properties.assignedAgent')}
              </label>
              <select
                name="assigned_agent_id"
                value={formData.assigned_agent_id}
                onChange={handleInputChange}
                className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                required
              >
                <option value="">{t('properties.selectAgent')}</option>
                {agents.map(agent => (
                  <option key={agent.id} value={agent.id}>
                    {agent.agent_name} ({agent.email})
                  </option>
                ))}
              </select>
            </div>
          </div>

          <Input
            label={t('properties.address')}
            name="address"
            value={formData.address}
            onChange={handleInputChange}
            required
          />

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <Input
              label={t('properties.city')}
              name="city"
              value={formData.city}
              onChange={handleInputChange}
              required
            />

            <Input
              label={t('properties.state')}
              name="state"
              value={formData.state}
              onChange={handleInputChange}
              maxLength={2}
              required
            />

            <Input
              label={t('properties.zipCode')}
              name="zip_code"
              value={formData.zip_code}
              onChange={handleInputChange}
              required
            />
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Input
              label={t('properties.price')}
              name="price"
              type="number"
              value={formData.price}
              onChange={handleInputChange}
            />

            <Input
              label={t('properties.mlsNumberLabel')}
              name="mls_number"
              value={formData.mls_number}
              onChange={handleInputChange}
            />
          </div>

          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            <Input
              label={t('properties.bedrooms')}
              name="bedrooms"
              type="number"
              value={formData.bedrooms}
              onChange={handleInputChange}
            />

            <Input
              label={t('properties.bathrooms')}
              name="bathrooms"
              type="number"
              step="0.5"
              value={formData.bathrooms}
              onChange={handleInputChange}
            />

            <Input
              label={t('properties.squareMeters')}
              name="square_feet"
              type="number"
              value={formData.square_feet}
              onChange={handleInputChange}
            />

            <Input
              label={t('properties.yearBuilt')}
              name="year_built"
              type="number"
              value={formData.year_built}
              onChange={handleInputChange}
            />
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                {t('properties.propertyType')}
              </label>
              <select
                name="property_type"
                value={formData.property_type}
                onChange={handleInputChange}
                className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value="single_family">{t('properties.singleFamily')}</option>
                <option value="condo">{t('properties.condo')}</option>
                <option value="townhouse">{t('properties.townhouse')}</option>
                <option value="multi_family">{t('properties.multiFamily')}</option>
              </select>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                {t('properties.listingStatus')}
              </label>
              <select
                name="listing_status"
                value={formData.listing_status}
                onChange={handleInputChange}
                className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value="active">{t('status.active')}</option>
                <option value="pending">{t('status.pending')}</option>
                <option value="sold">{t('status.sold')}</option>
                <option value="withdrawn">{t('status.withdrawn')}</option>
                <option value="expired">{t('status.expired')}</option>
              </select>
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              {t('properties.description')}
            </label>
            <textarea
              name="description"
              value={formData.description}
              onChange={handleInputChange}
              rows={4}
              className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder={t('properties.descriptionPlaceholder')}
            />
          </div>

          <div className="border-t pt-6 mt-2">
            <h3 className="text-lg font-semibold text-gray-800 mb-4">Brochure Fields</h3>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <Field label="Property Name (brochure title)" name="property_name" value={formData.property_name} onChange={handleInputChange} />
              <Field label="Listing Type Label (e.g. Long-term rental)" name="listing_type_label" value={formData.listing_type_label} onChange={handleInputChange} />
              <Field label="Rental Type (e.g. Furnished)" name="rental_type" value={formData.rental_type} onChange={handleInputChange} />
              <Field label="Room Count (total rooms)" name="room_count" type="number" value={formData.room_count} onChange={handleInputChange} />
              <Field label="Parking Spaces" name="parking_spaces" type="number" value={formData.parking_spaces} onChange={handleInputChange} />
              <Field label="Condition Rating" name="condition_rating" value={formData.condition_rating} onChange={handleInputChange} />
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">DPE Rating (A–G)</label>
                <select name="dpe_rating" value={formData.dpe_rating} onChange={handleInputChange}
                  className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500">
                  <option value="">—</option>
                  {['A','B','C','D','E','F','G'].map(l => <option key={l} value={l}>{l}</option>)}
                </select>
              </div>
              <Field label="View Description" name="view_description" value={formData.view_description} onChange={handleInputChange} />
              <Field label="District" name="district" value={formData.district} onChange={handleInputChange} />
              <Field label="Distance to Monaco" name="distance_to_monaco" value={formData.distance_to_monaco} onChange={handleInputChange} />
              <Field label="Terrace Description" name="terrace_description" value={formData.terrace_description} onChange={handleInputChange} />
              <Field label="Availability Period" name="availability_period" value={formData.availability_period} onChange={handleInputChange} />
              <Field label="Reference Number" name="reference_number" value={formData.reference_number} onChange={handleInputChange} />
            </div>
          </div>

          <div className="border-t pt-6 mt-2">
            <h3 className="text-lg font-semibold text-gray-800 mb-4">Public Site Content</h3>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Slug (public URL)</label>
                <div className="flex items-center gap-2">
                  <Input
                    name="slug"
                    value={formData.slug}
                    onChange={handleInputChange}
                    className="flex-1"
                    placeholder="e.g. sample-villa-cap-dail"
                  />
                  <Button type="button" onClick={generateSlug} className="px-3 py-2 text-sm bg-gray-100 text-gray-700 hover:bg-gray-200 border border-gray-300">
                    Generate
                  </Button>
                </div>
              </div>

              <div className="flex items-center gap-6 pt-6">
                <label className="flex items-center gap-2 text-sm text-gray-700">
                  <input type="checkbox" name="featured" checked={formData.featured} onChange={handleCheckboxChange} />
                  Featured (hero)
                </label>
                <label className="flex items-center gap-2 text-sm text-gray-700">
                  <input type="checkbox" name="public_listing" checked={formData.public_listing} onChange={handleCheckboxChange} />
                  Show on public site
                </label>
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mt-4">
              <Field label="Plot Size (m²)" name="plot_size" type="number" value={formData.plot_size} onChange={handleInputChange} />
              <Field label="Floor Count" name="floor_count" type="number" value={formData.floor_count} onChange={handleInputChange} />
              <Field label="Video URL" name="video_url" value={formData.video_url} onChange={handleInputChange} />
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4">
              <Field label="Map Latitude" name="map_lat" type="number" step="any" value={formData.map_lat} onChange={handleInputChange} />
              <Field label="Map Longitude" name="map_lng" type="number" step="any" value={formData.map_lng} onChange={handleInputChange} />
            </div>

            <div className="mt-4">
              <label className="block text-sm font-medium text-gray-700 mb-1">Terrain Description</label>
              <textarea name="terrain_description" value={formData.terrain_description} onChange={handleInputChange} rows={2}
                className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>

            <div className="mt-4">
              <label className="block text-sm font-medium text-gray-700 mb-1">House History (narrative)</label>
              <textarea name="house_history" value={formData.house_history} onChange={handleInputChange} rows={3}
                className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>

            <div className="mt-4">
              <label className="block text-sm font-medium text-gray-700 mb-1">Concept</label>
              <textarea name="concept_description" value={formData.concept_description} onChange={handleInputChange} rows={2}
                className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>

            <h4 className="text-sm font-semibold text-gray-700 mt-6 mb-2">Construction</h4>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <Field label="Walls" name="construction_walls" value={formData.construction_walls} onChange={handleInputChange} />
              <Field label="Insulation" name="construction_insulation" value={formData.construction_insulation} onChange={handleInputChange} />
              <Field label="Roof" name="construction_roof" value={formData.construction_roof} onChange={handleInputChange} />
              <Field label="Windows" name="construction_windows" value={formData.construction_windows} onChange={handleInputChange} />
            </div>

            <h4 className="text-sm font-semibold text-gray-700 mt-6 mb-2">Engineering</h4>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <Field label="Heating" name="engineering_heating" value={formData.engineering_heating} onChange={handleInputChange} />
              <Field label="Ventilation" name="engineering_ventilation" value={formData.engineering_ventilation} onChange={handleInputChange} />
              <Field label="Water Supply" name="engineering_water" value={formData.engineering_water} onChange={handleInputChange} />
              <Field label="Sewage" name="engineering_sewage" value={formData.engineering_sewage} onChange={handleInputChange} />
            </div>

            <h4 className="text-sm font-semibold text-gray-700 mt-6 mb-2">Room Layout</h4>
            <div className="space-y-2">
              {roomLayout.map((row, i) => (
                <div key={i} className="flex gap-2 items-start">
                  <input
                    value={row.name}
                    onChange={e => updateRoomRow(i, 'name', e.target.value)}
                    placeholder="Room name"
                    className="w-1/3 px-3 py-2 border border-gray-300 rounded-md"
                  />
                  <input
                    value={row.description}
                    onChange={e => updateRoomRow(i, 'description', e.target.value)}
                    placeholder="Description"
                    className="flex-1 px-3 py-2 border border-gray-300 rounded-md"
                  />
                  <Button type="button" onClick={() => removeRoomRow(i)} className="px-3 py-2 text-sm bg-red-50 text-red-600 hover:bg-red-100 border border-red-200">
                    Remove
                  </Button>
                </div>
              ))}
              <Button type="button" onClick={addRoomRow} className="px-3 py-2 text-sm bg-gray-100 text-gray-700 hover:bg-gray-200 border border-gray-300">
                + Add Room
              </Button>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Gallery Image URLs (one per line)</label>
                <textarea value={galleryUrls} onChange={e => setGalleryUrls(e.target.value)} rows={4}
                  className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500" />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Floor Plan Image URLs (one per line)</label>
                <textarea value={floorPlanUrls} onChange={e => setFloorPlanUrls(e.target.value)} rows={4}
                  className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500" />
              </div>
            </div>
          </div>

          <div className="flex justify-end space-x-3">
            <Button type="button" variant="outline" onClick={onClose}>
              {t('common.cancel')}
            </Button>
            <Button type="submit" disabled={loading}>
              {loading
                ? (isEditing ? t('properties.updating') : t('properties.creating'))
                : (isEditing ? t('properties.updateProperty') : t('properties.createProperty'))
              }
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  )
} 