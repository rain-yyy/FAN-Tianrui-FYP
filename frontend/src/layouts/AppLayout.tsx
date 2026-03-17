'use client';

import { History, Home, LogOut, UserRound, FileCode2, User } from 'lucide-react';
import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import { useState, useRef, useEffect } from 'react';
import { cn } from '@/lib/utils';
import { useAuth } from '@/providers/AuthProvider';
import { prefetchRouteModule } from '@/router/prefetch';

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
    <div className="min-h-screen flex flex-col bg-[#050505]">
      <header className="h-16 border-b border-white/10 bg-black/40 backdrop-blur-xl px-4 md:px-8 flex items-center justify-between sticky top-0 z-40">
        <div className="flex items-center gap-6">
          <button 
            onClick={() => navigate('/app/dashboard')}
            className="flex items-center gap-3 hover:opacity-80 transition-opacity group"
            aria-label="Go to Dashboard"
          >
            <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-fuchsia-500/20 to-cyan-500/20 border border-fuchsia-300/30 flex items-center justify-center group-hover:scale-105 transition-transform">
              <FileCode2 className="w-5 h-5 text-fuchsia-300" />
            </div>
            <div>
              <p className="text-base font-semibold bg-clip-text text-transparent bg-gradient-to-r from-white to-zinc-400">
                Project Wiki Generator
              </p>
            </div>
          </button>

          <div className="h-6 w-px bg-white/10 hidden md:block"></div>
          
          <nav className="hidden md:flex items-center gap-2">
            <NavLink
              to="/app/dashboard"
              onMouseEnter={() => prefetchRouteModule('dashboard')}
              className={({ isActive }) =>
                cn(
                  'flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm font-medium transition-all duration-200',
                  isActive
                    ? 'bg-white/10 text-white'
                    : 'text-zinc-400 hover:text-zinc-200 hover:bg-white/5'
                )
              }
            >
              <Home className="w-4 h-4" />
              Dashboard
            </NavLink>
            <NavLink
              to="/app/history"
              onMouseEnter={() => prefetchRouteModule('history')}
              className={({ isActive }) =>
                cn(
                  'flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm font-medium transition-all duration-200',
                  isActive
                    ? 'bg-white/10 text-white'
                    : 'text-zinc-400 hover:text-zinc-200 hover:bg-white/5'
                )
              }
            >
              <History className="w-4 h-4" />
              History
            </NavLink>
          </nav>
        </div>

        <div className="flex items-center gap-4">
          {/* Mobile Links */}
          <div className="md:hidden flex items-center gap-1.5 mr-2">
            <NavLink
              to="/app/dashboard"
              className={({ isActive }) =>
                cn(
                  'p-2 rounded-xl transition-all',
                  isActive ? 'bg-white/10 text-white shadow-sm' : 'text-zinc-400 hover:text-white hover:bg-white/5'
                )
              }
            >
              <Home className="w-5 h-5" />
            </NavLink>
            <NavLink
              to="/app/history"
              className={({ isActive }) =>
                cn(
                  'p-2 rounded-xl transition-all',
                  isActive ? 'bg-white/10 text-white shadow-sm' : 'text-zinc-400 hover:text-white hover:bg-white/5'
                )
              }
            >
              <History className="w-5 h-5" />
            </NavLink>
          </div>

          {/* User Profile Dropdown */}
          <div className="relative" ref={profileRef}>
            <button
              onClick={() => setIsProfileOpen(!isProfileOpen)}
              className="w-9 h-9 rounded-full bg-gradient-to-br from-zinc-800 to-zinc-900 border border-white/10 flex items-center justify-center hover:ring-2 hover:ring-fuchsia-500/50 hover:border-fuchsia-500/30 transition-all focus:outline-none group shadow-lg"
              aria-label="User menu"
            >
              <User className="w-5 h-5 text-zinc-300 group-hover:text-fuchsia-200 transition-colors" />
            </button>

            {isProfileOpen && (
              <div className="absolute right-0 mt-3 w-64 rounded-2xl border border-white/10 bg-[#121212]/95 backdrop-blur-xl shadow-2xl shadow-black/50 overflow-hidden py-1 z-50 animate-in fade-in slide-in-from-top-2 duration-200">
                <div className="px-4 py-4 border-b border-white/5 bg-white/[0.02]">
                  <p className="text-xs text-zinc-400 font-medium mb-1">Signed in as</p>
                  <p className="text-sm font-semibold text-zinc-100 truncate flex items-center gap-2" title={identity}>
                    <div className="w-2 h-2 rounded-full bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.5)]"></div>
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
                    className="w-full flex items-center gap-3 px-3 py-2.5 text-sm text-rose-400 hover:text-rose-300 hover:bg-rose-500/10 rounded-xl transition-all font-medium group"
                  >
                    <LogOut className="w-4 h-4 group-hover:-translate-x-0.5 transition-transform" />
                    Sign out
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

      <footer className="h-10 border-t border-white/10 bg-black/20 px-4 md:px-8 flex items-center justify-center text-xs text-zinc-500">
        Project Wiki Generator · Powered by AI
      </footer>
    </div>
  );
}
