'use client'

import { useRouter, usePathname } from 'next/navigation'
import { useAuth } from '@/contexts/AuthContext'
import { useLanguage } from '@/contexts/LanguageContext'
import { Button } from '@/components/ui/button'
import { ThemeToggle } from '@/components/ui/ThemeToggle'
import { Home, Building, Users, CheckSquare, TrendingUp, FileText, LogOut } from 'lucide-react'

interface MainNavigationProps {
  title?: string
}

export default function MainNavigation({ title = 'Real Estate CRM' }: MainNavigationProps) {
  const router = useRouter()
  const pathname = usePathname()
  const { user, signOut } = useAuth()
  const { t, language, toggleLanguage } = useLanguage()

  const navItems = [
    {
      label: t('nav.dashboard'),
      path: '/dashboard',
      icon: Home
    },
    {
      label: t('nav.properties'),
      path: '/properties',
      icon: Building
    },
    {
      label: t('nav.clients'),
      path: '/clients',
      icon: Users
    },
    {
      label: t('nav.tasks'),
      path: '/tasks',
      icon: CheckSquare
    },
    {
      label: t('nav.documents'),
      path: '/documents',
      icon: FileText
    },
    {
      label: t('nav.reports'),
      path: '/reports',
      icon: TrendingUp
    }
  ]

  const handleSignOut = async () => {
    try {
      await signOut()
      router.push('/login')
    } catch (error) {
      console.error('Error signing out:', error)
      // Still redirect to login even if sign out fails
      router.push('/login')
    }
  }

  const isActive = (path: string) => {
    if (path === '/dashboard') {
      return pathname === path
    }
    return pathname.startsWith(path)
  }

  return (
    <header className="bg-background shadow-sm border-b border-border">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex justify-between items-center py-4">
          {/* Left side - Logo/Title and Navigation */}
          <div className="flex items-center space-x-8">
            <div className="flex items-center space-x-3">
              <h1 className="text-xl font-semibold text-foreground whitespace-nowrap">{title}</h1>
            </div>
            
            <nav className="hidden sm:flex space-x-1">
              {navItems.map((item) => {
                const Icon = item.icon
                const active = isActive(item.path)
                
                return (
                  <button
                    key={item.path}
                    onClick={() => router.push(item.path)}
                    className={`group flex items-center px-4 py-2.5 rounded-lg text-sm font-medium transition-all duration-200 ease-in-out ${
                      active
                        ? 'bg-primary text-primary-foreground shadow-lg shadow-primary/25 transform scale-105'
                        : 'text-foreground hover:text-primary hover:bg-accent hover:shadow-md hover:scale-105 border border-transparent hover:border-primary/20'
                    }`}
                  >
                    <Icon className={`w-4 h-4 mr-2 transition-transform duration-200 ${
                      active ? 'text-primary-foreground' : 'text-muted-foreground group-hover:text-primary group-hover:scale-110'
                    }`} />
                    {item.label}
                  </button>
                )
              })}
            </nav>
          </div>

          {/* Right side - Theme toggle, User info and Sign Out */}
          <div className="flex items-center space-x-4 min-w-0">
            <span className="text-sm text-foreground font-medium hidden sm:block truncate max-w-[220px]" title={user?.email}>
              {t('nav.welcomeBack')}, {user?.email}
            </span>
            <button
              type="button"
              onClick={toggleLanguage}
              className="flex items-center rounded-lg border border-border text-xs font-semibold overflow-hidden"
              aria-label="Toggle language"
            >
              <span className={`px-2 py-1 ${language === 'ru' ? 'bg-primary text-primary-foreground' : 'text-foreground'}`}>RU</span>
              <span className={`px-2 py-1 ${language === 'en' ? 'bg-primary text-primary-foreground' : 'text-foreground'}`}>EN</span>
            </button>
            <ThemeToggle />
            <Button
              variant="outline"
              size="sm"
              onClick={handleSignOut}
              className="flex items-center text-foreground border-border hover:text-destructive hover:border-destructive/50 hover:bg-destructive/10 transition-all duration-200 hover:shadow-md"
            >
              <LogOut className="w-4 h-4 mr-2 transition-transform duration-200 hover:scale-110" />
              {t('nav.signOut')}
            </Button>
          </div>
        </div>

        {/* Mobile Navigation */}
        <nav className="sm:hidden border-t border-border pt-4 pb-4">
          <div className="flex space-x-1 overflow-x-auto">
            {navItems.map((item) => {
              const Icon = item.icon
              const active = isActive(item.path)
              
              return (
                <button
                  key={item.path}
                  onClick={() => router.push(item.path)}
                  className={`group flex items-center px-3 py-2 rounded-lg text-xs font-medium transition-all duration-200 ease-in-out whitespace-nowrap ${
                    active
                      ? 'bg-primary text-primary-foreground shadow-lg shadow-primary/25'
                      : 'text-foreground hover:text-primary hover:bg-accent hover:shadow-md border border-transparent hover:border-primary/20'
                  }`}
                >
                  <Icon className={`w-4 h-4 mr-1 transition-transform duration-200 ${
                    active ? 'text-primary-foreground' : 'text-muted-foreground group-hover:text-primary group-hover:scale-110'
                  }`} />
                  {item.label}
                </button>
              )
            })}
          </div>
        </nav>
      </div>
    </header>
  )
} 