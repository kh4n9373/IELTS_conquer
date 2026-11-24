-- The review safety net re-queues only questions the worker can explain (approved key) and at most
-- once per cooldown, so an open, polling review page cannot drive LLM calls without limit.
create or replace function app.attempt_review_row(p_attempt uuid)
returns jsonb language sql stable security definer set search_path = public, pg_temp as $$
  select jsonb_build_object(
    'testId', g.test_id,
    'jobActive', exists (select 1 from explain_jobs j where j.test_id = g.test_id
                         and j.state in ('queued', 'running')),
    'lastJobAt', (select max(j.created_at) from explain_jobs j where j.test_id = g.test_id),
    'questions', coalesce((select jsonb_agg(jsonb_build_object(
        'number', q.number, 'type', q.question_type, 'sectionTitle', s.title,
        'prompt', q.prompt, 'options', to_jsonb(q.options),
        'hasKey', exists (select 1 from keys.answer_key_items i
                          where i.question_number = q.number and cardinality(i.answers) > 0
                            and i.version_id = (select v.id from keys.answer_key_versions v
                                                where v.test_id = q.test_id and v.approved_at is not null
                                                order by v.version desc limit 1)),
        'explanation', (select jsonb_build_object('correct', e.correct, 'options', e.options)
                        from keys.question_explanations e where e.question_id = q.id))
        order by q.number)
      from questions q join sections s on s.id = q.section_id where q.test_id = g.test_id), '[]')
  )
  from attempts a join assignments g on g.id = a.assignment_id where a.id = p_attempt
$$;
