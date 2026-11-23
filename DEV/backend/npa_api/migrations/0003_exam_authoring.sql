-- Exam authoring. Every table carries teacher_id and is isolated by RLS.
-- Composite foreign keys on (parent_id, teacher_id) stop a row from pointing at another tenant.

create table tests (
  id uuid primary key default gen_random_uuid(),
  teacher_id uuid not null references users (id),
  title text not null check (length(trim(title)) between 1 and 200),
  test_type text not null check (test_type in ('ielts_standard', 'course')),
  source_type text not null check (source_type in ('self_authored', 'licensed', 'commercial', 'other')),
  status text not null default 'draft' check (status in ('draft', 'published', 'archived')),
  duration_minutes int not null check (duration_minutes between 1 and 300),
  published_at timestamptz,
  created_at timestamptz not null default now(),
  unique (id, teacher_id)
);
create index tests_teacher_idx on tests (teacher_id, created_at desc);

create table test_assets (
  id uuid primary key default gen_random_uuid(),
  test_id uuid not null,
  teacher_id uuid not null,
  kind text not null check (kind in ('student_pdf', 'key_source')),
  storage_key text not null,
  sha256 text not null,
  byte_size bigint not null check (byte_size >= 0),
  page_count int check (page_count > 0),
  has_text_layer boolean,
  created_at timestamptz not null default now(),
  unique (test_id, kind),
  unique (id, teacher_id),
  foreign key (test_id, teacher_id) references tests (id, teacher_id) on delete cascade
);

create table sections (
  id uuid primary key default gen_random_uuid(),
  test_id uuid not null,
  teacher_id uuid not null,
  position int not null check (position > 0),
  title text not null,
  suggested_minutes int check (suggested_minutes > 0),
  passage_pages int[] not null default '{}',
  question_pages int[] not null default '{}',
  units_primary text[] not null default '{}',
  units_secondary text[] not null default '{}',
  unique (test_id, position),
  unique (id, test_id, teacher_id),
  foreign key (test_id, teacher_id) references tests (id, teacher_id) on delete cascade
);

create table questions (
  id uuid primary key default gen_random_uuid(),
  test_id uuid not null,
  teacher_id uuid not null,
  section_id uuid not null,
  number int not null check (number > 0),
  question_type text not null check (question_type in (
    'matching_features', 'matching_headings', 'matching_information', 'sentence_endings',
    'multiple_choice', 'tfng', 'ynng', 'table_completion', 'note_completion',
    'summary_completion', 'sentence_completion', 'short_answer', 'diagram_label'
  )),
  word_limit int check (word_limit > 0),
  unique (test_id, number),
  foreign key (section_id, test_id, teacher_id) references sections (id, test_id, teacher_id) on delete cascade
);
create index questions_section_idx on questions (section_id);

create table page_texts (
  asset_id uuid not null,
  teacher_id uuid not null,
  page int not null check (page > 0),
  text text not null,
  primary key (asset_id, page),
  foreign key (asset_id, teacher_id) references test_assets (id, teacher_id) on delete cascade
);

create table ocr_jobs (
  id uuid primary key default gen_random_uuid(),
  asset_id uuid not null,
  teacher_id uuid not null,
  state text not null default 'queued' check (state in ('queued', 'running', 'done', 'failed')),
  attempts int not null default 0,
  lease_until timestamptz,
  provider_job_id text,
  error_code text,
  created_at timestamptz not null default now(),
  finished_at timestamptz,
  foreign key (asset_id, teacher_id) references test_assets (id, teacher_id) on delete cascade
);
create index ocr_jobs_claim_idx on ocr_jobs (state, lease_until);
create index ocr_jobs_asset_idx on ocr_jobs (asset_id);

-- Answer keys live in their own schema so no other role gets a path to them.
create schema keys;
grant usage on schema keys to npa_teacher;

create table keys.answer_key_versions (
  id uuid primary key default gen_random_uuid(),
  test_id uuid not null,
  teacher_id uuid not null,
  version int not null check (version > 0),
  approved_at timestamptz,
  approved_by uuid references users (id),
  unique (test_id, version),
  unique (id, teacher_id),
  -- Only the owning teacher approves, and approval is always a pair.
  check (approved_by is null or approved_by = teacher_id),
  check ((approved_at is null) = (approved_by is null)),
  -- Restrict: deleting a test must never silently erase its keys.
  foreign key (test_id, teacher_id) references tests (id, teacher_id) on delete restrict
);

create table keys.answer_key_items (
  version_id uuid not null,
  teacher_id uuid not null,
  question_number int not null check (question_number > 0),
  answers text[] not null default '{}',
  origin text not null check (origin in ('manual', 'text_layer', 'scan')),
  flagged boolean not null default false,
  source_page int check (source_page > 0),
  source_bbox jsonb,
  reviewed_at timestamptz,
  reviewed_by uuid references users (id),
  check (reviewed_by is null or reviewed_by = teacher_id),
  check ((reviewed_at is null) = (reviewed_by is null)),
  primary key (version_id, question_number),
  foreign key (version_id, teacher_id) references keys.answer_key_versions (id, teacher_id) on delete cascade
);

create function app.reject_approved_key_delete() returns trigger language plpgsql as $$
begin
  if old.approved_at is not null then
    raise exception 'approved answer key versions are immutable';
  end if;
  return old;
end $$;
create trigger answer_key_versions_keep_approved before delete on keys.answer_key_versions
  for each row execute function app.reject_approved_key_delete();
revoke execute on function app.reject_approved_key_delete() from public;

-- Approval freezes a version: the only allowed update is approved_at/approved_by going from null to set,
-- and identity columns (id, test_id, teacher_id, version) never change, even in that same update.
create function app.reject_approved_key_version_update() returns trigger language plpgsql as $$
begin
  if old.approved_at is not null
     or new.id is distinct from old.id
     or new.test_id is distinct from old.test_id
     or new.teacher_id is distinct from old.teacher_id
     or new.version is distinct from old.version then
    raise exception 'approved answer key versions are immutable';
  end if;
  return new;
end $$;
create trigger answer_key_versions_keep_approved_update before update on keys.answer_key_versions
  for each row execute function app.reject_approved_key_version_update();
revoke execute on function app.reject_approved_key_version_update() from public;

-- Items of an approved version cannot be inserted, changed, moved or deleted.
-- Cascade deletes of an unapproved version pass: the parent row is already gone by then.
create function app.reject_approved_key_item_change() returns trigger language plpgsql as $$
begin
  if tg_op in ('UPDATE', 'DELETE') and exists (
    select 1 from keys.answer_key_versions v where v.id = old.version_id and v.approved_at is not null
  ) then
    raise exception 'approved answer key items are immutable';
  end if;
  if tg_op in ('INSERT', 'UPDATE') and exists (
    select 1 from keys.answer_key_versions v where v.id = new.version_id and v.approved_at is not null
  ) then
    raise exception 'approved answer key items are immutable';
  end if;
  if tg_op = 'DELETE' then
    return old;
  end if;
  return new;
end $$;
create trigger answer_key_items_keep_approved before insert or update or delete on keys.answer_key_items
  for each row execute function app.reject_approved_key_item_change();
revoke execute on function app.reject_approved_key_item_change() from public;

grant select, insert, update, delete on
  tests, test_assets, sections, questions, page_texts, ocr_jobs,
  keys.answer_key_versions, keys.answer_key_items
  to npa_teacher;

alter table tests enable row level security;
alter table test_assets enable row level security;
alter table sections enable row level security;
alter table questions enable row level security;
alter table page_texts enable row level security;
alter table ocr_jobs enable row level security;
alter table keys.answer_key_versions enable row level security;
alter table keys.answer_key_items enable row level security;

create policy tests_teacher on tests for all
  to npa_teacher using (teacher_id = app.user_id()) with check (teacher_id = app.user_id());
create policy test_assets_teacher on test_assets for all
  to npa_teacher using (teacher_id = app.user_id()) with check (teacher_id = app.user_id());
create policy sections_teacher on sections for all
  to npa_teacher using (teacher_id = app.user_id()) with check (teacher_id = app.user_id());
create policy questions_teacher on questions for all
  to npa_teacher using (teacher_id = app.user_id()) with check (teacher_id = app.user_id());
create policy page_texts_teacher on page_texts for all
  to npa_teacher using (teacher_id = app.user_id()) with check (teacher_id = app.user_id());
create policy ocr_jobs_teacher on ocr_jobs for all
  to npa_teacher using (teacher_id = app.user_id()) with check (teacher_id = app.user_id());
create policy answer_key_versions_teacher on keys.answer_key_versions for all
  to npa_teacher using (teacher_id = app.user_id()) with check (teacher_id = app.user_id());
create policy answer_key_items_teacher on keys.answer_key_items for all
  to npa_teacher using (teacher_id = app.user_id()) with check (teacher_id = app.user_id());

-- The worker has no user identity, so it claims and settles jobs through definer functions.
create function app.ocr_claim(p_now timestamptz, p_lease_until timestamptz)
returns table (id uuid, asset_id uuid, teacher_id uuid, attempts int, provider_job_id text)
language sql security definer set search_path = public, pg_temp as $$
  update ocr_jobs j
  set state = 'running', attempts = j.attempts + 1, lease_until = p_lease_until
  where j.id = (
    select c.id from ocr_jobs c
    where c.state = 'queued' or (c.state = 'running' and c.lease_until < p_now)
    order by c.created_at
    limit 1
    for update skip locked
  )
  returning j.id, j.asset_id, j.teacher_id, j.attempts, j.provider_job_id
$$;

create function app.ocr_finish(p_job uuid, p_state text, p_error text, p_now timestamptz)
returns void language plpgsql security definer set search_path = public, pg_temp as $$
begin
  if p_state is null or p_state not in ('done', 'failed', 'queued') then
    raise exception 'invalid_state';
  end if;
  update ocr_jobs
  set state = p_state,
      error_code = p_error,
      lease_until = null,
      finished_at = case when p_state = 'queued' then null else p_now end
  where id = p_job;
end $$;

-- 0002's blanket revoke only covered functions that existed then.
revoke execute on function
  app.ocr_claim(timestamptz, timestamptz),
  app.ocr_finish(uuid, text, text, timestamptz)
  from public;
grant execute on function
  app.ocr_claim(timestamptz, timestamptz),
  app.ocr_finish(uuid, text, text, timestamptz)
  to npa_app;
