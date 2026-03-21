'use client';

import { useEffect } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import Auth from '@/components/Auth';
import { useAuth } from '@/providers/AuthProvider';
import { prefetchRouteModule } from '@/router/prefetch';

export default function LoginPage() {
  const { user, isLoading } = useAuth();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  useEffect(() => {
    prefetchRouteModule('dashboard');
  }, []);

  useEffect(() => {
    if (isLoading || !user) return;
    const nextPath = searchParams.get('next');
    navigate(nextPath || '/app/dashboard', { replace: true });
  }, [isLoading, navigate, searchParams, user]);

  return (
    <div className="min-h-screen flex flex-col items-center justify-center p-4">
      <div className="w-full max-w-3xl space-y-8">
        <div className="text-center space-y-4">
          <h1 className="text-4xl md:text-6xl font-bold tracking-tight bg-clip-text text-transparent bg-gradient-to-r from-white via-fuchsia-200 to-cyan-200">
            Project Wiki Generator
          </h1>
          <p className="text-lg text-zinc-300 max-w-xl mx-auto">Sign in to open the wiki workspace.</p>
        </div>
        <div className="rounded-3xl border border-white/10 bg-[linear-gradient(145deg,rgba(18,12,36,0.9),rgba(9,9,20,0.88))] backdrop-blur-2xl p-4 md:p-6 shadow-[0_20px_80px_rgba(124,58,237,0.22)]">
          <Auth />
        </div>
      </div>
    </div>
  );
}
