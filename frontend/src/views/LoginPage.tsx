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
          <h1 className="text-4xl md:text-6xl font-bold tracking-tight text-stone-900">
            Project Wiki Generator
          </h1>
          <p className="text-lg text-stone-600 max-w-xl mx-auto">Sign in to open the wiki workspace.</p>
        </div>
        <div className="rounded-3xl border border-stone-200 bg-white/90 backdrop-blur-md p-4 md:p-6 shadow-lg shadow-stone-900/5">
          <Auth />
        </div>
      </div>
    </div>
  );
}
