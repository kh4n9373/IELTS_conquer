-- Exam room: assignments, attempts, answers, grading. Additive only: no drop, rename or change of old columns,
-- so the running M2 API keeps working. Students never write tables directly; they go through definer functions.

alter table tests add column grading_rules text not null default 'standard-1'
  check (grading_rules in ('standard-1', 'pilot-1'));
alter table questions add column answer_group int check (answer_group > 0);
alter table classes add column is_trial boolean not null default false;
alter table users add column is_trial boolean not null default false;

create table assignments (
  id uuid primary key default gen_random_uuid(),
  teacher_id uuid not null,
  test_id uuid not null,
  class_id uuid not null,
  opens_at timestamptz not null,
  due_at timestamptz not null,
  max_attempts int not null default 1 check (max_attempts between 1 and 5),
  mode text not null default 'exam' check (mode in ('exam', 'practice')),
  release_policy text not null default 'after_submit'
    check (release_policy in ('after_submit', 'after_last', 'scheduled')),
  release_at timestamptz,
  is_trial boolean not null default false,
  cancelled_at timestamptz,
  created_at timestamptz not null default now(),
  check (due_at > opens_at),
  check ((release_policy = 'scheduled') = (release_at is not null)),
  unique (id, teacher_id),
  foreign key (test_id, teacher_id) references tests (id, teacher_id),
  foreign key (class_id, teacher_id) references classes (id, teacher_id)
);
create index assignments_teacher_idx on assignments (teacher_id, created_at desc);

create table assignment_recipients (
  assignment_id uuid not null,
  teacher_id uuid not null,
  student_id uuid not null references users (id),
  primary key (assignment_id, student_id),
  foreign key (assignment_id, teacher_id) references assignments (id, teacher_id)
);
create index assignment_recipients_student_idx on assignment_recipients (student_id);

create table attempts (
  id uuid primary key default gen_random_uuid(),
  assignment_id uuid not null,
  teacher_id uuid not null,
  student_id uuid not null,
  number int not null check (number > 0),
  mode text not null check (mode in ('exam', 'practice')),
  status text not null default 'in_progress' check (status in ('in_progress', 'submitted', 'auto_submitted')),
  started_at timestamptz not null,
  deadline_at timestamptz not null,
  finalized_at timestamptz,
  writer_token text,
  device_type text not null default 'unknown' check (device_type in ('desktop', 'tablet', 'mobile', 'unknown')),
  unique (assignment_id, student_id, number),
  unique (id, teacher_id),
  foreign key (assignment_id, teacher_id) references assignments (id, teacher_id),
  foreign key (assignment_id, student_id) references assignment_recipients (assignment_id, student_id)
);
create unique index attempts_one_open_idx on attempts (assignment_id, student_id) where status = 'in_progress';
create index attempts_student_idx on attempts (student_id);
create index attempts_due_idx on attempts (deadline_at) where status = 'in_progress';

create table attempt_answers (
  attempt_id uuid not null,
  teacher_id uuid not null,
  question_number int not null check (question_number > 0),
  value text,
  marked_for_review boolean not null default false,
  seq bigint not null,
  saved_at timestamptz not null default now(),
  primary key (attempt_id, question_number),
  foreign key (attempt_id, teacher_id) references attempts (id, teacher_id)
);

-- Answers that arrived after the grace period: never graded, but never lost.
create table late_answers (
  id uuid primary key default gen_random_uuid(),
  attempt_id uuid not null,
  teacher_id uuid not null,
  question_number int not null check (question_number > 0),
  value text,
  received_at timestamptz not null default now(),
  foreign key (attempt_id, teacher_id) references attempts (id, teacher_id)
);
create index late_answers_attempt_idx on late_answers (attempt_id);

create table keys.grading_runs (
  id uuid primary key default gen_random_uuid(),
  attempt_id uuid not null,
  teacher_id uuid not null,
  run int not null check (run > 0),
  key_version_id uuid not null,
  rules_version text not null check (rules_version in ('standard-1', 'pilot-1')),
  test_type text not null check (test_type in ('ielts_standard', 'course')),
  correct int not null check (correct >= 0),
  total int not null check (total >= 0),
  percent numeric(5, 2) not null check (percent between 0 and 100),
  band text,
  band_table_version text,
  created_at timestamptz not null default now(),
  unique (attempt_id, run),
  unique (id, teacher_id),
  check (test_type = 'ielts_standard' or (band is null and band_table_version is null)),
  foreign key (attempt_id, teacher_id) references attempts (id, teacher_id),
  foreign key (key_version_id, teacher_id) references keys.answer_key_versions (id, teacher_id)
);

create table keys.grading_items (
  run_id uuid not null,
  teacher_id uuid not null,
  question_number int not null check (question_number > 0),
  given text,
  correct boolean not null,
  reason text not null,
  accepted text[] not null default '{}',
  primary key (run_id, question_number),
  foreign key (run_id, teacher_id) references keys.grading_runs (id, teacher_id)
);

-- The recorded test_type must match the real test, and only a standard IELTS test may carry a band.
create function app.check_grading_run() returns trigger language plpgsql set search_path = public, pg_temp as $$
declare v_type text;
begin
  select t.test_type into v_type
  from attempts a
  join assignments g on g.id = a.assignment_id
  join tests t on t.id = g.test_id
  where a.id = new.attempt_id;
  if v_type is distinct from new.test_type then
    raise exception 'grading run test_type does not match the test';
  end if;
  if v_type <> 'ielts_standard' and (new.band is not null or new.band_table_version is not null) then
    raise exception 'a band is only allowed for a standard IELTS test';
  end if;
  return new;
end $$;
create trigger grading_runs_check before insert on keys.grading_runs
  for each row execute function app.check_grading_run();

create function app.reject_grading_change() returns trigger language plpgsql as $$
begin
  raise exception 'grading records are immutable';
end $$;
create trigger grading_runs_immutable before update or delete on keys.grading_runs
  for each row execute function app.reject_grading_change();
create trigger grading_items_immutable before update or delete on keys.grading_items
  for each row execute function app.reject_grading_change();
revoke execute on function app.check_grading_run(), app.reject_grading_change() from public;

grant select, insert, update on assignments, assignment_recipients to npa_teacher;
grant select on attempts, attempt_answers, late_answers, keys.grading_runs, keys.grading_items to npa_teacher;
grant select on assignments, assignment_recipients, attempts, attempt_answers to npa_student;

alter table assignments enable row level security;
alter table assignment_recipients enable row level security;
alter table attempts enable row level security;
alter table attempt_answers enable row level security;
alter table late_answers enable row level security;
alter table keys.grading_runs enable row level security;
alter table keys.grading_items enable row level security;

create policy assignments_teacher on assignments for all
  to npa_teacher using (teacher_id = app.user_id()) with check (teacher_id = app.user_id());
create policy recipients_teacher on assignment_recipients for all
  to npa_teacher using (teacher_id = app.user_id()) with check (teacher_id = app.user_id());
create policy attempts_teacher on attempts for select to npa_teacher using (teacher_id = app.user_id());
create policy answers_teacher on attempt_answers for select to npa_teacher using (teacher_id = app.user_id());
create policy late_answers_teacher on late_answers for select to npa_teacher using (teacher_id = app.user_id());
create policy grading_runs_teacher on keys.grading_runs for select
  to npa_teacher using (teacher_id = app.user_id());
create policy grading_items_teacher on keys.grading_items for select
  to npa_teacher using (teacher_id = app.user_id());

create policy recipients_student on assignment_recipients for select
  to npa_student using (student_id = app.user_id());
create policy assignments_student on assignments for select to npa_student using (
  exists (select 1 from assignment_recipients r where r.assignment_id = id and r.student_id = app.user_id())
);
create policy attempts_student on attempts for select to npa_student using (student_id = app.user_id());
create policy answers_student on attempt_answers for select to npa_student using (
  exists (select 1 from attempts a where a.id = attempt_id and a.student_id = app.user_id())
);

-- Student functions. Identity comes from app.user_id(); every one checks it owns the attempt.
-- A student removed from the class cannot enter again; answers already saved are still graded.
create function app.still_in_class(p_assignment uuid)
returns boolean language sql stable security definer set search_path = public, pg_temp as $$
  select exists (
    select 1 from assignments g
    join class_members m on m.class_id = g.class_id and m.student_id = app.user_id() and m.removed_at is null
    where g.id = p_assignment)
$$;

create function app.attempt_start(p_assignment uuid, p_now timestamptz, p_device text)
returns uuid language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v_student uuid := app.user_id();
  v_asg assignments;
  v_minutes int;
  v_id uuid;
begin
  perform 1 from users where id = v_student and role = 'student' and status = 'active';
  if not found then raise exception 'not_allowed'; end if;
  select a.* into v_asg from assignments a
  join assignment_recipients r on r.assignment_id = a.id and r.student_id = v_student
  where a.id = p_assignment for update of a;
  if not found or v_asg.cancelled_at is not null then raise exception 'not_found'; end if;
  if not app.still_in_class(p_assignment) then raise exception 'removed_from_class'; end if;
  -- Resuming an open attempt is always allowed; its deadline is already fixed.
  select id into v_id from attempts
  where assignment_id = p_assignment and student_id = v_student and status = 'in_progress';
  if found then return v_id; end if;
  if (select status from tests where id = v_asg.test_id) <> 'published' then
    raise exception 'test_archived';
  end if;
  if p_now < v_asg.opens_at then raise exception 'not_open'; end if;
  if p_now >= v_asg.due_at then raise exception 'closed'; end if;
  if (select count(*) from attempts where assignment_id = p_assignment and student_id = v_student)
     >= v_asg.max_attempts then
    raise exception 'no_attempts_left';
  end if;
  select duration_minutes into v_minutes from tests where id = v_asg.test_id;
  insert into attempts (assignment_id, teacher_id, student_id, number, mode, started_at, deadline_at, device_type)
  values (
    p_assignment, v_asg.teacher_id, v_student,
    (select count(*) + 1 from attempts where assignment_id = p_assignment and student_id = v_student),
    v_asg.mode, p_now, least(p_now + make_interval(mins => v_minutes), v_asg.due_at),
    case when p_device in ('desktop', 'tablet', 'mobile') then p_device else 'unknown' end
  ) returning id into v_id;
  return v_id;
end $$;

-- The device opened last takes the write token; older devices are refused by attempt_save.
create function app.attempt_claim(p_attempt uuid, p_now timestamptz)
returns text language plpgsql security definer set search_path = public, pg_temp as $$
declare v_token text := replace(gen_random_uuid()::text || gen_random_uuid()::text, '-', ''); v_att attempts;
begin
  select * into v_att from attempts where id = p_attempt and student_id = app.user_id() for update;
  if not found then raise exception 'not_found'; end if;
  if not app.still_in_class(v_att.assignment_id) then raise exception 'removed_from_class'; end if;
  if v_att.status <> 'in_progress' then raise exception 'attempt_closed'; end if;
  update attempts set writer_token = v_token where id = p_attempt;
  return v_token;
end $$;

-- Allow-listed fields only: no key, no grading, no teacher data.
create function app.attempt_room(p_attempt uuid, p_now timestamptz)
returns jsonb language plpgsql stable security definer set search_path = public, pg_temp as $$
declare v_att attempts; v_test tests; v_pages int;
begin
  select * into v_att from attempts where id = p_attempt and student_id = app.user_id();
  if not found then raise exception 'not_found'; end if;
  if not app.still_in_class(v_att.assignment_id) then raise exception 'removed_from_class'; end if;
  select t.* into v_test from tests t join assignments g on g.test_id = t.id where g.id = v_att.assignment_id;
  select page_count into v_pages from test_assets where test_id = v_test.id and kind = 'student_pdf';
  return jsonb_build_object(
    'title', v_test.title,
    'durationMinutes', v_test.duration_minutes,
    'pageCount', v_pages,
    'questionCount', (select count(*) from questions where test_id = v_test.id),
    'mode', v_att.mode,
    'status', v_att.status,
    'deadlineAt', v_att.deadline_at,
    'serverNow', p_now,
    'sections', coalesce((select jsonb_agg(jsonb_build_object(
        'position', s.position, 'title', s.title,
        'passagePages', s.passage_pages, 'questionPages', s.question_pages) order by s.position)
      from sections s where s.test_id = v_test.id), '[]'),
    'questions', coalesce((select jsonb_agg(jsonb_build_object(
        'number', q.number, 'type', q.question_type, 'wordLimit', q.word_limit, 'group', q.answer_group)
        order by q.number) from questions q where q.test_id = v_test.id), '[]'),
    'answers', coalesce((select jsonb_agg(jsonb_build_object(
        'number', x.question_number, 'value', x.value, 'marked', x.marked_for_review, 'seq', x.seq)
        order by x.question_number) from attempt_answers x where x.attempt_id = p_attempt), '[]')
  );
end $$;

-- items: [{number, value, marked, seq}]. Per question, a lower or equal seq never overwrites a newer one.
-- Within the grace period after the deadline answers still count; later ones go to late_answers.
create function app.attempt_save(p_attempt uuid, p_token text, p_items jsonb, p_now timestamptz, p_grace_seconds int)
returns jsonb language plpgsql security definer set search_path = public, pg_temp as $$
declare v_att attempts; v_item jsonb; v_saved int := 0; v_late int := 0; v_status text := 'ok';
begin
  if jsonb_typeof(p_items) is distinct from 'array' or jsonb_array_length(p_items) > 200 then
    raise exception 'invalid_items';
  end if;
  select * into v_att from attempts where id = p_attempt and student_id = app.user_id() for update;
  if not found then raise exception 'not_found'; end if;
  if not app.still_in_class(v_att.assignment_id) then raise exception 'removed_from_class'; end if;
  if v_att.writer_token is distinct from p_token then
    return jsonb_build_object('status', 'superseded', 'saved', 0, 'late', 0);
  end if;
  for v_item in select * from jsonb_array_elements(p_items) loop
    if not exists (
      select 1 from questions q join assignments g on g.test_id = q.test_id
      where g.id = v_att.assignment_id and q.number = (v_item ->> 'number')::int
    ) or length(coalesce(v_item ->> 'value', '')) > 2000 then
      raise exception 'invalid_items';
    end if;
    if v_att.status = 'in_progress' and p_now <= v_att.deadline_at + make_interval(secs => p_grace_seconds) then
      insert into attempt_answers (attempt_id, teacher_id, question_number, value, marked_for_review, seq, saved_at)
      values (p_attempt, v_att.teacher_id, (v_item ->> 'number')::int, v_item ->> 'value',
              coalesce((v_item ->> 'marked')::boolean, false), (v_item ->> 'seq')::bigint, p_now)
      on conflict (attempt_id, question_number) do update
        set value = excluded.value, marked_for_review = excluded.marked_for_review,
            seq = excluded.seq, saved_at = excluded.saved_at
        where attempt_answers.seq < excluded.seq;
      v_saved := v_saved + 1;
    else
      insert into late_answers (attempt_id, teacher_id, question_number, value, received_at)
      values (p_attempt, v_att.teacher_id, (v_item ->> 'number')::int, v_item ->> 'value', p_now);
      v_late := v_late + 1;
      v_status := 'late';
    end if;
  end loop;
  return jsonb_build_object('status', v_status, 'saved', v_saved, 'late', v_late);
end $$;

-- Gate for acting on an own attempt (submit): 404 for anyone else's, 403 once removed from the class.
create function app.attempt_assert_active(p_attempt uuid)
returns void language plpgsql stable security definer set search_path = public, pg_temp as $$
declare v_assignment uuid;
begin
  select assignment_id into v_assignment from attempts where id = p_attempt and student_id = app.user_id();
  if not found then raise exception 'not_found'; end if;
  if not app.still_in_class(v_assignment) then raise exception 'removed_from_class'; end if;
end $$;

create function app.attempt_pdf_key(p_attempt uuid, p_now timestamptz)
returns text language plpgsql stable security definer set search_path = public, pg_temp as $$
declare v_key text; v_att attempts;
begin
  select * into v_att from attempts where id = p_attempt and student_id = app.user_id();
  if not found then raise exception 'not_found'; end if;
  if not app.still_in_class(v_att.assignment_id) then raise exception 'removed_from_class'; end if;
  if v_att.status <> 'in_progress' then raise exception 'attempt_closed'; end if;
  select x.storage_key into v_key
  from assignments g join test_assets x on x.test_id = g.test_id and x.kind = 'student_pdf'
  where g.id = v_att.assignment_id;
  if v_key is null then raise exception 'not_found'; end if;
  return v_key;
end $$;

-- API-only functions. Grading locks the row, so a second caller sees a finished attempt and stops.
create function app.attempt_lock_for_grading(p_attempt uuid)
returns jsonb language plpgsql security definer set search_path = public, pg_temp as $$
declare v_att attempts; v_test tests; v_key uuid;
begin
  select * into v_att from attempts where id = p_attempt for update;
  if not found or v_att.status <> 'in_progress' then return null; end if;
  select t.* into v_test from tests t join assignments g on g.test_id = t.id where g.id = v_att.assignment_id;
  select id into v_key from keys.answer_key_versions
  where test_id = v_test.id and approved_at is not null order by version desc limit 1;
  return jsonb_build_object(
    'teacherId', v_att.teacher_id, 'studentId', v_att.student_id, 'deadlineAt', v_att.deadline_at,
    'testType', v_test.test_type, 'rules', v_test.grading_rules, 'keyVersionId', v_key,
    'questions', coalesce((select jsonb_agg(jsonb_build_object(
        'number', q.number, 'type', q.question_type, 'wordLimit', q.word_limit, 'group', q.answer_group,
        'accepted', coalesce(k.answers, '{}')) order by q.number)
      from questions q
      left join keys.answer_key_items k on k.version_id = v_key and k.question_number = q.number
      where q.test_id = v_test.id), '[]'),
    'answers', coalesce((select jsonb_agg(jsonb_build_object('number', x.question_number, 'value', x.value)
        order by x.question_number) from attempt_answers x where x.attempt_id = p_attempt), '[]')
  );
end $$;

create function app.attempt_due(p_cutoff timestamptz, p_limit int)
returns setof uuid language sql stable security definer set search_path = public, pg_temp as $$
  select id from attempts where status = 'in_progress' and deadline_at < p_cutoff
  order by deadline_at limit greatest(p_limit, 0)
$$;

-- run: {keyVersionId, rulesVersion, testType, correct, total, percent, band, bandTableVersion}
-- items: [{number, given, correct, reason, accepted}]
create function app.attempt_store_grade(p_attempt uuid, p_status text, p_now timestamptz, p_run jsonb, p_items jsonb)
returns uuid language plpgsql security definer set search_path = public, pg_temp as $$
declare v_att attempts; v_run uuid;
begin
  if p_status is null or p_status not in ('submitted', 'auto_submitted') then raise exception 'invalid_status'; end if;
  select * into v_att from attempts where id = p_attempt for update;
  if not found or v_att.status <> 'in_progress' then raise exception 'already_final'; end if;
  update attempts set status = p_status, finalized_at = p_now where id = p_attempt;
  insert into keys.grading_runs (attempt_id, teacher_id, run, key_version_id, rules_version, test_type,
                                 correct, total, percent, band, band_table_version)
  values (p_attempt, v_att.teacher_id, 1, (p_run ->> 'keyVersionId')::uuid, p_run ->> 'rulesVersion',
          p_run ->> 'testType', (p_run ->> 'correct')::int, (p_run ->> 'total')::int,
          (p_run ->> 'percent')::numeric, p_run ->> 'band', p_run ->> 'bandTableVersion')
  returning id into v_run;
  insert into keys.grading_items (run_id, teacher_id, question_number, given, correct, reason, accepted)
  select v_run, v_att.teacher_id, (i ->> 'number')::int, i ->> 'given', (i ->> 'correct')::boolean,
         i ->> 'reason', coalesce(array(select jsonb_array_elements_text(i -> 'accepted')), '{}')
  from jsonb_array_elements(p_items) i;
  return v_run;
end $$;

-- Raw result for the API, which decides whether it may be shown (release policy).
create function app.attempt_result_row(p_attempt uuid)
returns jsonb language sql stable security definer set search_path = public, pg_temp as $$
  select jsonb_build_object(
    'attemptId', a.id, 'studentId', a.student_id, 'teacherId', a.teacher_id, 'status', a.status,
    'number', a.number, 'mode', a.mode, 'finalizedAt', a.finalized_at,
    'assignment', jsonb_build_object('id', g.id, 'releasePolicy', g.release_policy, 'releaseAt', g.release_at,
                                     'dueAt', g.due_at, 'maxAttempts', g.max_attempts),
    'isTrial', (g.is_trial or exists (select 1 from classes c where c.id = g.class_id and c.is_trial)
                or exists (select 1 from users u where u.id = a.student_id and u.is_trial)),
    'sections', coalesce((select jsonb_agg(jsonb_build_object('position', s.position, 'title', s.title,
              'numbers', (select coalesce(jsonb_agg(q.number order by q.number), '[]'::jsonb)
                          from questions q where q.section_id = s.id)) order by s.position)
            from sections s where s.test_id = g.test_id), '[]'),
    'facts', jsonb_build_object(
      'ownFinal', (select count(*) from attempts x where x.assignment_id = g.id
                   and x.student_id = a.student_id and x.status <> 'in_progress'),
      'openAttempts', (select count(*) from attempts x where x.assignment_id = g.id and x.status = 'in_progress'),
      -- A removed student who never started cannot finish, so they do not hold back the group.
      'activeRecipients', (select count(*) from assignment_recipients r where r.assignment_id = g.id
                           and (exists (select 1 from attempts x where x.assignment_id = g.id
                                        and x.student_id = r.student_id)
                                or exists (select 1 from class_members m where m.class_id = g.class_id
                                           and m.student_id = r.student_id and m.removed_at is null))),
      'recipientsDone', (select count(*) from assignment_recipients r where r.assignment_id = g.id
                         and (select count(*) from attempts x where x.assignment_id = g.id
                              and x.student_id = r.student_id and x.status <> 'in_progress') >= g.max_attempts)),
    'run', (select jsonb_build_object('run', r.run, 'keyVersionId', r.key_version_id,
              'rulesVersion', r.rules_version, 'testType', r.test_type, 'correct', r.correct, 'total', r.total,
              'percent', r.percent, 'band', r.band, 'bandTableVersion', r.band_table_version)
            from keys.grading_runs r where r.attempt_id = a.id order by r.run desc limit 1),
    'items', coalesce((select jsonb_agg(jsonb_build_object('number', i.question_number, 'given', i.given,
              'correct', i.correct, 'reason', i.reason, 'accepted', i.accepted) order by i.question_number)
            from keys.grading_items i
            where i.run_id = (select r.id from keys.grading_runs r where r.attempt_id = a.id
                              order by r.run desc limit 1)), '[]')
  )
  from attempts a join assignments g on g.id = a.assignment_id where a.id = p_attempt
$$;

revoke execute on function app.still_in_class(uuid) from public;
revoke execute on function
  app.attempt_start(uuid, timestamptz, text), app.attempt_claim(uuid, timestamptz),
  app.attempt_room(uuid, timestamptz), app.attempt_save(uuid, text, jsonb, timestamptz, int),
  app.attempt_pdf_key(uuid, timestamptz), app.attempt_assert_active(uuid), app.attempt_lock_for_grading(uuid),
  app.attempt_due(timestamptz, int), app.attempt_store_grade(uuid, text, timestamptz, jsonb, jsonb),
  app.attempt_result_row(uuid)
  from public;
grant execute on function
  app.attempt_start(uuid, timestamptz, text), app.attempt_claim(uuid, timestamptz),
  app.attempt_room(uuid, timestamptz), app.attempt_save(uuid, text, jsonb, timestamptz, int),
  app.attempt_pdf_key(uuid, timestamptz), app.attempt_assert_active(uuid)
  to npa_student;
grant execute on function
  app.attempt_lock_for_grading(uuid), app.attempt_due(timestamptz, int),
  app.attempt_store_grade(uuid, text, timestamptz, jsonb, jsonb), app.attempt_result_row(uuid)
  to npa_app;

-- Students cannot read tests; this gives them the title of assignments they received, nothing else.
create function app.my_assignment_titles()
returns table (assignment_id uuid, title text)
language sql stable security definer set search_path = public, pg_temp as $$
  select g.id, t.title from assignments g
  join assignment_recipients r on r.assignment_id = g.id and r.student_id = app.user_id()
  join tests t on t.id = g.test_id
$$;
revoke execute on function app.my_assignment_titles() from public;
grant execute on function app.my_assignment_titles() to npa_student;
