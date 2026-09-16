'use client';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { 
  Plus, 
  FileText, 
  FolderOpen, 
  Settings, 
  Archive,
  Download,
  Upload,
  Users,
  Building2,
  Calendar
} from 'lucide-react';
import Link from 'next/link';

interface DocumentSummary {
  document_name: string;
  document_type: string | null;
  document_status: string | null;
  created_at: string | null;
}

interface DocumentsSidebarProps {
  className?: string;
  documents?: DocumentSummary[];
}

export default function DocumentsSidebar({ className = "", documents = [] }: DocumentsSidebarProps) {
  const quickActions = [
    {
      label: 'Create Document',
      href: '/documents/create',
      icon: Plus,
      color: 'bg-blue-500 hover:bg-blue-600 text-white'
    },
    {
      label: 'Browse Templates',
      href: '/documents/templates',
      icon: FolderOpen,
      color: 'bg-gray-500 hover:bg-gray-600 text-white'
    },
    {
      label: 'Document Settings',
      href: '/documents/settings',
      icon: Settings,
      color: 'bg-gray-500 hover:bg-gray-600 text-white'
    }
  ];

  // These used to be hardcoded demo numbers/events (12/8/6/4 and three
  // fabricated activity lines) that showed up even for a brand-new account
  // with zero real documents — misleading, since nothing here ever
  // happened. Derived from the real `documents` list passed in by the
  // parent page instead.
  const documentTypeDefs = [
    { label: 'Listing Agreements', icon: FileText, filter: 'listing_agreement' },
    { label: 'Purchase Agreements', icon: FileText, filter: 'purchase_agreement' },
    { label: 'Lease Agreements', icon: FileText, filter: 'lease_agreement' },
    { label: 'Disclosures', icon: FileText, filter: 'disclosure' }
  ];
  const documentTypes = documentTypeDefs.map(def => ({
    ...def,
    count: documents.filter(d => d.document_type === def.filter).length
  }));

  function timeAgo(iso: string | null) {
    if (!iso) return '';
    const ms = Date.now() - new Date(iso).getTime();
    const mins = Math.floor(ms / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins} min${mins === 1 ? '' : 's'} ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`;
    const days = Math.floor(hours / 24);
    return `${days} day${days === 1 ? '' : 's'} ago`;
  }

  const recentActivity = [...documents]
    .sort((a, b) => new Date(b.created_at || 0).getTime() - new Date(a.created_at || 0).getTime())
    .slice(0, 3)
    .map(doc => ({
      action: doc.document_status === 'signed' ? 'Document signed'
        : doc.document_status === 'finalized' ? 'Document finalized'
        : 'Document created',
      document: doc.document_name,
      time: timeAgo(doc.created_at),
      icon: doc.document_status === 'signed' ? FileText : doc.document_status === 'finalized' ? Download : Plus
    }));

  return (
    <div className={`space-y-6 ${className}`}>
      {/* Quick Actions */}
      <Card>
        <CardHeader>
          <h3 className="text-lg font-semibold">Quick Actions</h3>
        </CardHeader>
        <CardContent className="space-y-2">
          {quickActions.map((action, index) => {
            const IconComponent = action.icon;
            return (
              <Link key={index} href={action.href}>
                <Button 
                  variant="outline" 
                  className={`w-full justify-start ${action.color}`}
                >
                  <IconComponent className="w-4 h-4 mr-2" />
                  {action.label}
                </Button>
              </Link>
            );
          })}
        </CardContent>
      </Card>

      {/* Document Types */}
      <Card>
        <CardHeader>
          <h3 className="text-lg font-semibold">Document Types</h3>
        </CardHeader>
        <CardContent className="space-y-1">
          {documentTypes.map((type, index) => {
            const IconComponent = type.icon;
            return (
              <button
                key={index}
                className="group w-full flex items-center justify-between p-2 text-sm hover:bg-blue-50 hover:text-blue-600 rounded-md transition-all duration-200 hover:scale-105 hover:shadow-sm"
              >
                <div className="flex items-center">
                  <IconComponent className="w-4 h-4 mr-2 text-gray-500 group-hover:text-blue-600 transition-colors duration-200" />
                  {type.label}
                </div>
                <span className="text-xs bg-gray-100 text-gray-600 px-2 py-1 rounded-full">
                  {type.count}
                </span>
              </button>
            );
          })}
        </CardContent>
      </Card>

      {/* Recent Activity */}
      <Card>
        <CardHeader>
          <h3 className="text-lg font-semibold">Recent Activity</h3>
        </CardHeader>
        <CardContent className="space-y-3">
          {recentActivity.length === 0 ? (
            <p className="text-sm text-gray-500">No recent activity</p>
          ) : recentActivity.map((activity, index) => {
            const IconComponent = activity.icon;
            return (
              <div key={index} className="flex items-start space-x-3">
                <div className="flex-shrink-0">
                  <IconComponent className="w-4 h-4 text-gray-500 mt-0.5" />
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-gray-900">{activity.action}</p>
                  <p className="text-xs text-gray-500 truncate">{activity.document}</p>
                  <p className="text-xs text-gray-400">{activity.time}</p>
                </div>
              </div>
            );
          })}
        </CardContent>
      </Card>

      {/* Shortcuts */}
      <Card>
        <CardHeader>
          <h3 className="text-lg font-semibold">Shortcuts</h3>
        </CardHeader>
        <CardContent className="space-y-2">
          <Link href="/clients">
            <Button variant="ghost" size="sm" className="w-full justify-start">
              <Users className="w-4 h-4 mr-2" />
              View Clients
            </Button>
          </Link>
          <Link href="/properties">
            <Button variant="ghost" size="sm" className="w-full justify-start">
              <Building2 className="w-4 h-4 mr-2" />
              View Properties
            </Button>
          </Link>
          <Link href="/tasks">
            <Button variant="ghost" size="sm" className="w-full justify-start">
              <Calendar className="w-4 h-4 mr-2" />
              View Tasks
            </Button>
          </Link>
        </CardContent>
      </Card>
    </div>
  );
}