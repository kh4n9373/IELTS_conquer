-- LLM explanations per question, written once per test and reused by every attempt.
-- They reveal the key, so they live in the keys schema: students only see them through the
-- release-gated review endpoint.

create table keys.question_explanations (
  question_id uuid primary key references questions (id) on delete cascade,
  test_id uuid not null,
  teacher_id uuid not null,
  content_hash text not null,
  correct text not null check (length(correct) <= 2000),
  options jsonb not null default '{}',
  source text not null default 'ai' check (source in ('ai', 'teacher')),
  provider text,
  created_at timestamptz not null default now(),
  foreign key (test_id, teacher_id) references tests (id, teacher_id) on delete cascade
);
create index question_explanations_test_idx on keys.question_explanations (test_id);

create table explain_jobs (
  id uuid primary key default gen_random_uuid(),
  test_id uuid not null,
  teacher_id uuid not null,
  state text not null default 'queued' check (state in ('queued', 'running', 'done', 'failed')),
  attempts int not null default 0,
  lease_until timestamptz,
  error_code text check (error_code ~ '^[a-z0-9_]{1,64}$'),
  created_at timestamptz not null default now(),
  finished_at timestamptz,
  foreign key (test_id, teacher_id) references tests (id, teacher_id) on delete cascade
);
create index explain_jobs_claim_idx on explain_jobs (state, lease_until);
create unique index explain_jobs_active_idx on explain_jobs (test_id) where state in ('queued', 'running');

grant select, insert, update, delete on keys.question_explanations, explain_jobs to npa_teacher;
alter table keys.question_explanations enable row level security;
alter table explain_jobs enable row level security;
create policy question_explanations_teacher on keys.question_explanations for all
  to npa_teacher using (teacher_id = app.user_id()) with check (teacher_id = app.user_id());
create policy explain_jobs_teacher on explain_jobs for all
  to npa_teacher using (teacher_id = app.user_id()) with check (teacher_id = app.user_id());

-- Same shape as app.ocr_claim: the worker has no identity.
create function app.explain_claim(p_now timestamptz, p_lease_until timestamptz)
returns table (id uuid, test_id uuid, teacher_id uuid, attempts int)
language plpgsql security definer set search_path = public, pg_temp as $$
#variable_conflict use_column
begin
  update explain_jobs j
  set state = 'failed', error_code = 'lease_expired', lease_until = null, finished_at = p_now
  where j.id in (
    select c.id from explain_jobs c
    where c.state = 'running' and c.lease_until < p_now and c.attempts >= 3
    for update skip locked
  );

  return query
  update explain_jobs j
  set state = 'running', attempts = j.attempts + 1, lease_until = p_lease_until
  where j.id = (
    select c.id from explain_jobs c
    where c.attempts < 3
      and ((c.state = 'queued' and (c.lease_until is null or c.lease_until <= p_now))
        or (c.state = 'running' and c.lease_until < p_now))
    order by c.created_at
    limit 1
    for update skip locked
  )
  returning j.id, j.test_id, j.teacher_id, j.attempts;
end $$;

create function app.explain_finish(
  p_job uuid, p_attempt int, p_state text, p_error text, p_retry_at timestamptz, p_now timestamptz
)
returns boolean language plpgsql security definer set search_path = public, pg_temp as $$
begin
  if p_state is null or p_state not in ('failed', 'queued') then
    raise exception 'invalid_state';
  end if;
  update explain_jobs
  set state = p_state,
      error_code = p_error,
      lease_until = case when p_state = 'queued' then p_retry_at else null end,
      finished_at = case when p_state = 'queued' then null else p_now end
  where id = p_job and state = 'running' and attempts = p_attempt;
  return found;
end $$;

-- No-op while a job for the test is queued or running.
create function app.explain_enqueue(p_test uuid)
returns void language sql security definer set search_path = public, pg_temp as $$
  insert into explain_jobs (test_id, teacher_id)
  select t.id, t.teacher_id from tests t where t.id = p_test
  on conflict (test_id) where state in ('queued', 'running') do nothing
$$;

-- Question text and stored explanations for an attempt's test. The caller checks ownership and
-- release first (same as app.attempt_result_row).
create function app.attempt_review_row(p_attempt uuid)
returns jsonb language sql stable security definer set search_path = public, pg_temp as $$
  select jsonb_build_object(
    'testId', g.test_id,
    'jobActive', exists (select 1 from explain_jobs j where j.test_id = g.test_id
                         and j.state in ('queued', 'running')),
    'questions', coalesce((select jsonb_agg(jsonb_build_object(
        'number', q.number, 'type', q.question_type, 'sectionTitle', s.title,
        'prompt', q.prompt, 'options', to_jsonb(q.options),
        'explanation', (select jsonb_build_object('correct', e.correct, 'options', e.options)
                        from keys.question_explanations e where e.question_id = q.id))
        order by q.number)
      from questions q join sections s on s.id = q.section_id where q.test_id = g.test_id), '[]')
  )
  from attempts a join assignments g on g.id = a.assignment_id where a.id = p_attempt
$$;

revoke execute on function
  app.explain_claim(timestamptz, timestamptz),
  app.explain_finish(uuid, int, text, text, timestamptz, timestamptz),
  app.explain_enqueue(uuid),
  app.attempt_review_row(uuid)
  from public;
grant execute on function
  app.explain_claim(timestamptz, timestamptz),
  app.explain_finish(uuid, int, text, text, timestamptz, timestamptz),
  app.explain_enqueue(uuid),
  app.attempt_review_row(uuid)
  to npa_app;
