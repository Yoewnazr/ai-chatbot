-- ============================================================
-- V3 — Skema Pangkalan Data Supabase (PostgreSQL)
-- Jalankan ini di Supabase Dashboard → SQL Editor → New query.
-- ============================================================

-- 1) Jadual perbualan (diikat dengan user_id dari Supabase Auth)
create table if not exists public.conversations (
  id         uuid primary key,
  user_id    uuid not null references auth.users (id) on delete cascade,
  title      text not null default 'Perbualan Baharu',
  updated_at bigint not null default (extract(epoch from now()) * 1000)::bigint
);

-- 2) Jadual mesej bagi setiap perbualan
create table if not exists public.messages (
  id            bigint generated always as identity primary key,
  conversation_id uuid not null references public.conversations (id) on delete cascade,
  idx           integer not null,             -- susunan mesej dalam perbualan
  role          text not null check (role in ('user', 'assistant', 'system')),
  content       jsonb not null,               -- string teks ATAU array multimodal (teks + imej)
  created_at    bigint not null default (extract(epoch from now()) * 1000)::bigint,
  unique (conversation_id, idx)
);

create index if not exists messages_conversation_idx on public.messages (conversation_id, idx);

-- 3) Dayakan Row Level Security (WAJIB untuk keselamatan)
alter table public.conversations enable row level security;
alter table public.messages enable row level security;

-- 4) Dasar RLS: pengguna hanya boleh akses data milik sendiri
create policy "Baca perbualan sendiri"
  on public.conversations for select
  using (auth.uid() = user_id);

create policy "Cipta perbualan sendiri"
  on public.conversations for insert
  with check (auth.uid() = user_id);

create policy "Kemas kini perbualan sendiri"
  on public.conversations for update
  using (auth.uid() = user_id);

create policy "Padam perbualan sendiri"
  on public.conversations for delete
  using (auth.uid() = user_id);

-- Mesej: akses diwarisi melalui conversation_id (guna exists)
create policy "Baca mesej sendiri"
  on public.messages for select
  using (exists (
    select 1 from public.conversations c
    where c.id = messages.conversation_id and c.user_id = auth.uid()
  ));

create policy "Cipta mesej sendiri"
  on public.messages for insert
  with check (exists (
    select 1 from public.conversations c
    where c.id = messages.conversation_id and c.user_id = auth.uid()
  ));

create policy "Kemas kini mesej sendiri"
  on public.messages for update
  using (exists (
    select 1 from public.conversations c
    where c.id = messages.conversation_id and c.user_id = auth.uid()
  ));

create policy "Padam mesej sendiri"
  on public.messages for delete
  using (exists (
    select 1 from public.conversations c
    where c.id = messages.conversation_id and c.user_id = auth.uid()
  ));

-- 5) Dayakan Realtime untuk penyelarasan langsung di sidebar
alter publication supabase_realtime add table public.conversations;
alter publication supabase_realtime add table public.messages;
