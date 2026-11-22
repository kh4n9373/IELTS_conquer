-- Roles are cluster-wide, so creation must be idempotent.
do $$
declare r text;
begin
  if not exists (select 1 from pg_roles where rolname = 'npa_app') then
    create role npa_app login noinherit;
  end if;
  foreach r in array array['npa_student', 'npa_teacher', 'npa_admin'] loop
    if not exists (select 1 from pg_roles where rolname = r) then
      execute format('create role %I nologin', r);
    end if;
    execute format('grant %I to npa_app', r);
  end loop;
end $$;

revoke all on schema public from public;
grant usage on schema public to npa_app, npa_student, npa_teacher, npa_admin;

create schema app;
grant usage on schema app to npa_app, npa_student, npa_teacher, npa_admin;

create function app.user_id() returns uuid language sql stable as
  $$ select nullif(current_setting('app.user_id', true), '')::uuid $$;
create function app.teacher_id() returns uuid language sql stable as
  $$ select nullif(current_setting('app.teacher_id', true), '')::uuid $$;

create table users (
  id uuid primary key default gen_random_uuid(),
  email text not null,
  password_hash text not null,
  display_name text not null,
  role text not null check (role in ('student', 'teacher', 'admin')),
  status text not null default 'active' check (status in ('active', 'locked')),
  teacher_id uuid references users (id),
  age_confirmed_at timestamptz,
  created_at timestamptz not null default now(),
  check ((role = 'student') = (teacher_id is not null))
);
create unique index users_email_lower_key on users (lower(email));
create index users_teacher_idx on users (teacher_id);

create table sessions (
  id uuid primary key default gen_random_uuid(),
  token_hash text not null unique,
  user_id uuid not null references users (id) on delete cascade,
  expires_at timestamptz not null,
  revoked_at timestamptz,
  created_at timestamptz not null default now()
);
create index sessions_user_idx on sessions (user_id);

create table password_resets (
  token_hash text primary key,
  user_id uuid not null references users (id) on delete cascade,
  expires_at timestamptz not null,
  used_at timestamptz,
  created_at timestamptz not null default now()
);

create table consents (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references users (id) on delete cascade default app.user_id(),
  purpose text not null check (purpose in ('core_learning', 'ai_processing')),
  terms_version text not null,
  granted boolean not null,
  created_at timestamptz not null default clock_timestamp()
);
create index consents_latest_idx on consents (user_id, purpose, created_at desc);

create table classes (
  id uuid primary key default gen_random_uuid(),
  teacher_id uuid not null references users (id),
  name text not null check (length(trim(name)) between 1 and 120),
  status text not null default 'active' check (status in ('active', 'archived')),
  created_at timestamptz not null default now(),
  unique (id, teacher_id)
);
create index classes_teacher_idx on classes (teacher_id);

-- teacher_id is denormalized so policies never need to query classes (no RLS recursion).
create table class_members (
  class_id uuid not null,
  teacher_id uuid not null,
  student_id uuid not null references users (id),
  joined_at timestamptz not null default now(),
  removed_at timestamptz,
  primary key (class_id, student_id),
  foreign key (class_id, teacher_id) references classes (id, teacher_id)
);
create index class_members_student_idx on class_members (student_id);

create table invites (
  id uuid primary key default gen_random_uuid(),
  class_id uuid not null,
  teacher_id uuid not null,
  code text not null unique,
  expires_at timestamptz not null,
  disabled_at timestamptz,
  created_at timestamptz not null default now(),
  foreign key (class_id, teacher_id) references classes (id, teacher_id)
);
create index invites_class_idx on invites (class_id);

create table audit_log (
  id uuid primary key default gen_random_uuid(),
  actor_id uuid references users (id) default app.user_id(),
  teacher_id uuid references users (id) default app.teacher_id(),
  action text not null,
  entity text not null,
  entity_id text not null,
  before jsonb,
  after jsonb,
  created_at timestamptz not null default clock_timestamp()
);
create index audit_log_teacher_idx on audit_log (teacher_id, created_at desc);

create function app.reject_audit_change() returns trigger language plpgsql as $$
begin
  raise exception 'audit_log is append-only';
end $$;
create trigger audit_log_append_only before update or delete on audit_log
  for each row execute function app.reject_audit_change();

-- Column grants: password_hash is deliberately absent.
grant select (id, email, display_name, role, status, teacher_id, age_confirmed_at, created_at)
  on users to npa_student, npa_teacher, npa_admin;
grant update (status) on users to npa_admin;
grant select, insert on consents to npa_student, npa_teacher;
grant select, insert, update on classes to npa_teacher;
grant select on classes to npa_student;
grant select, insert, update on class_members to npa_teacher;
grant select on class_members to npa_student;
grant select, insert, update on invites to npa_teacher;
grant select, insert on audit_log to npa_student, npa_teacher, npa_admin;

alter table users enable row level security;
alter table sessions enable row level security;
alter table password_resets enable row level security;
alter table consents enable row level security;
alter table classes enable row level security;
alter table class_members enable row level security;
alter table invites enable row level security;
alter table audit_log enable row level security;

create policy users_self on users for select
  to npa_student, npa_teacher, npa_admin using (id = app.user_id());
create policy users_teacher_students on users for select
  to npa_teacher using (teacher_id = app.user_id());
create policy users_admin_read on users for select
  to npa_admin using (role in ('teacher', 'admin'));
create policy users_admin_update on users for update
  to npa_admin using (role = 'teacher') with check (role = 'teacher');

create policy consents_own on consents for all
  to npa_student, npa_teacher using (user_id = app.user_id()) with check (user_id = app.user_id());

create policy classes_teacher on classes for all
  to npa_teacher using (teacher_id = app.user_id()) with check (teacher_id = app.user_id());
create policy classes_student on classes for select to npa_student using (
  exists (
    select 1 from class_members m
    where m.class_id = classes.id and m.student_id = app.user_id() and m.removed_at is null
  )
);

create policy members_teacher on class_members for all
  to npa_teacher using (teacher_id = app.user_id()) with check (
    teacher_id = app.user_id()
    and exists (select 1 from users u where u.id = student_id and u.teacher_id = app.user_id())
  );
create policy members_student on class_members for select
  to npa_student using (student_id = app.user_id());

create policy invites_teacher on invites for all
  to npa_teacher using (teacher_id = app.user_id()) with check (teacher_id = app.user_id());

create policy audit_insert on audit_log for insert
  to npa_student, npa_teacher, npa_admin with check (
    actor_id = app.user_id() and teacher_id is not distinct from app.teacher_id()
  );
create policy audit_teacher_read on audit_log for select
  to npa_teacher using (teacher_id = app.user_id());
create policy audit_admin_read on audit_log for select to npa_admin using (true);
