'use client';

import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { supabase } from '@/lib/supabase';

export default function AuthCallbackPage() {
  const navigate = useNavigate();

  useEffect(() => {
    // Supabase automatically detects the hash (#access_token=...) on page load.
    // We just need to wait for the SIGNED_IN event to fire, then redirect.
    const { data: { subscription } } = supabase.auth.onAuthStateChange((event) => {
      if (event === 'SIGNED_IN') {
        navigate('/app/dashboard', { replace: true });
      }
    });

    // Also handle the case where the session is already established
    // (e.g., user refreshes this page after sign-in was processed)
    supabase.auth.getSession().then(({ data: { session } }) => {
      if (session) {
        navigate('/app/dashboard', { replace: true });
      }
    });

    return () => {
      subscription.unsubscribe();
    };
  }, [navigate]);

  return (
    <div className="min-h-screen flex items-center justify-center bg-stone-50">
      <div className="text-center space-y-3">
        <div className="w-8 h-8 border-2 border-stone-300 border-t-stone-700 rounded-full animate-spin mx-auto" />
        <p className="text-sm text-stone-500">Signing you in...</p>
      </div>
    </div>
  );
}
