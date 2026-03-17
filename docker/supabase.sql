-- WARNING: This schema is for context only and is not meant to be run.
-- Table order and constraints may not be valid for execution.

CREATE TABLE public.chat_history (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL,
  repo_url text NOT NULL,
  preview_text text,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  updated_at timestamp with time zone NOT NULL DEFAULT now(),
  title text,
  CONSTRAINT chat_history_pkey PRIMARY KEY (id),
  CONSTRAINT chat_history_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.profiles(id)
);
CREATE TABLE public.chat_messages (
  id uuid NOT NULL DEFAULT uuid_generate_v4(),
  chat_id uuid NOT NULL,
  role text NOT NULL CHECK (role = ANY (ARRAY['user'::text, 'assistant'::text])),
  content text NOT NULL,
  metadata jsonb DEFAULT '{}'::jsonb,
  created_at timestamp with time zone DEFAULT now(),
  CONSTRAINT chat_messages_pkey PRIMARY KEY (id),
  CONSTRAINT chat_messages_chat_id_fkey FOREIGN KEY (chat_id) REFERENCES public.chat_history(id)
);
CREATE TABLE public.profiles (
  id uuid NOT NULL,
  email text,
  phone text,
  full_name text,
  avatar_url text,
  updated_at timestamp with time zone DEFAULT now(),
  CONSTRAINT profiles_pkey PRIMARY KEY (id),
  CONSTRAINT profiles_id_fkey FOREIGN KEY (id) REFERENCES auth.users(id)
);
CREATE TABLE public.repositories (
  repo_url text NOT NULL,
  r2_structure_url text,
  r2_content_urls ARRAY,
  last_updated timestamp with time zone NOT NULL DEFAULT now(),
  vector_store_path text,
  description text,
  CONSTRAINT repositories_pkey PRIMARY KEY (repo_url)
);
CREATE TABLE public.tasks (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL,
  task_id text NOT NULL,
  repo_url text NOT NULL,
  status text DEFAULT 'pending'::text,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  last_updated timestamp with time zone,
  progress text,
  current_step text,
  result json,
  error text,
  CONSTRAINT tasks_pkey PRIMARY KEY (id),
  CONSTRAINT tasks_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id),
  CONSTRAINT tasks_user_id_fkey1 FOREIGN KEY (user_id) REFERENCES public.profiles(id)
);