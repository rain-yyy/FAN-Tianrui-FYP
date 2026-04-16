'use client';

import { History, Home, LogOut, FileCode2, User } from 'lucide-react';
import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import { useState, useRef, useEffect } from 'react';
import { cn } from '@/lib/utils';
import { useAuth } from '@/providers/AuthProvider';
import { prefetchRouteModule } from '@/router/prefetch';
import { t } from '@/lib/i18n';

export default function AppLayout() {
  const { user, signOut } = useAuth();
  const identity = user?.email ?? user?.phone ?? user?.id ?? 'Unknown User';
  const navigate = useNavigate();
  const [isProfileOpen, setIsProfileOpen] = useState(false);
  const profileRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (profileRef.current && !profileRef.current.contains(event.target as Node)) {
        setIsProfileOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  return (
    <div className="min-h-screen flex flex-col bg-[var(--background)]">
      <header className="h-16 border-b border-stone-200 bg-white/80 backdrop-blur-md px-4 md:px-8 flex items-center justify-between sticky top-0 z-40 shadow-sm shadow-stone-900/5">
        <div className="flex items-center gap-6">
          <button
            onClick={() => navigate('/app/dashboard')}
            className="flex items-center gap-3 hover:opacity-90 transition-opacity group"
            aria-label="Go to Dashboard"
          >
            <div className="w-9 h-9 rounded-xl bg-sky-50 border border-sky-200/80 flex items-center justify-center group-hover:scale-105 transition-transform shadow-sm">
              {/* <FileCode2 className="w-5 h-5 text-sky-700" /> */}
              <img src="/logo.png" alt="GitReader" className="w-5 h-5" />
            </div>
            <div>
              <p className="text-base font-semibold text-stone-900 tracking-tight">
                GitReader
              </p>
            </div>
          </button>

          <div className="h-6 w-px bg-stone-200 hidden md:block" />

          <nav className="hidden md:flex items-center gap-2">
            <NavLink
              to="/app/dashboard"
              onMouseEnter={() => prefetchRouteModule('dashboard')}
              className={({ isActive }) =>
                cn(
                  'flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm font-medium transition-all duration-200',
                  isActive
                    ? 'bg-sky-100 text-sky-900 border border-sky-200/80'
                    : 'text-stone-600 hover:text-stone-900 hover:bg-stone-100 border border-transparent'
                )
              }
            >
              <Home className="w-4 h-4" />
              {t('dashboard')}
            </NavLink>
            <NavLink
              to="/app/history"
              onMouseEnter={() => prefetchRouteModule('history')}
              className={({ isActive }) =>
                cn(
                  'flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm font-medium transition-all duration-200',
                  isActive
                    ? 'bg-sky-100 text-sky-900 border border-sky-200/80'
                    : 'text-stone-600 hover:text-stone-900 hover:bg-stone-100 border border-transparent'
                )
              }
            >
              <History className="w-4 h-4" />
              {t('history')}
            </NavLink>
          </nav>
        </div>

        <div className="flex items-center gap-4">
          <div className="md:hidden flex items-center gap-1.5 mr-2">
            <NavLink
              to="/app/dashboard"
              className={({ isActive }) =>
                cn(
                  'p-2 rounded-xl transition-all border',
                  isActive
                    ? 'bg-sky-100 text-sky-900 border-sky-200 shadow-sm'
                    : 'text-stone-600 hover:text-stone-900 hover:bg-stone-100 border-transparent'
                )
              }
            >
              <Home className="w-5 h-5" />
            </NavLink>
            <NavLink
              to="/app/history"
              className={({ isActive }) =>
                cn(
                  'p-2 rounded-xl transition-all border',
                  isActive
                    ? 'bg-sky-100 text-sky-900 border-sky-200 shadow-sm'
                    : 'text-stone-600 hover:text-stone-900 hover:bg-stone-100 border-transparent'
                )
              }
            >
              <History className="w-5 h-5" />
            </NavLink>
          </div>

          <div className="relative" ref={profileRef}>
            <button
              onClick={() => setIsProfileOpen(!isProfileOpen)}
              className="w-9 h-9 rounded-full bg-stone-100 border border-stone-200 flex items-center justify-center hover:ring-2 hover:ring-sky-200 hover:border-sky-300 transition-all focus:outline-none group shadow-sm"
              aria-label="User menu"
            >
              <User className="w-5 h-5 text-stone-600 group-hover:text-sky-800 transition-colors" />
            </button>

            {isProfileOpen && (
              <div className="absolute right-0 mt-3 w-64 rounded-2xl border border-stone-200 bg-white shadow-xl shadow-stone-900/10 overflow-hidden py-1 z-50 animate-in fade-in slide-in-from-top-2 duration-200">
                <div className="px-4 py-4 border-b border-stone-100 bg-stone-50/80">
                  <p className="text-xs text-stone-500 font-medium mb-1">{t('signedInAs')}</p>
                  <p className="text-sm font-semibold text-stone-900 truncate flex items-center gap-2" title={identity}>
                    <span className="inline-block w-2 h-2 rounded-full bg-emerald-500 shrink-0" />
                    {identity}
                  </p>
                </div>
                <div className="p-2">
                  <button
                    type="button"
                    onClick={() => {
                      setIsProfileOpen(false);
                      void signOut();
                    }}
                    className="w-full flex items-center gap-3 px-3 py-2.5 text-sm text-rose-700 hover:text-rose-800 hover:bg-rose-50 rounded-xl transition-all font-medium group"
                  >
                    <LogOut className="w-4 h-4 group-hover:-translate-x-0.5 transition-transform" />
                    {t('signOut')}
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      </header>

      <main className="flex-1 w-full max-w-[1600px] mx-auto p-4 md:p-8 flex flex-col relative">
        <Outlet />
      </main>

      <footer className="h-10 border-t border-stone-200 bg-white/60 px-4 md:px-8 flex items-center justify-center text-xs text-stone-500">
        Project Wiki Generator · Powered by AI
      </footer>
    </div>
  );
}
