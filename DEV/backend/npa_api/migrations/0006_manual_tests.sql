-- A test is authored either from a PDF or typed in the app (passage text, prompts and options).
alter table tests add column authoring text not null default 'pdf' check (authoring in ('pdf', 'manual'));
alter table sections add column body text not null default '' check (length(body) <= 50000);
alter table questions
  add column prompt text not null default '' check (length(prompt) <= 2000),
  add column options text[] not null default '{}' check (cardinality(options) <= 26);

alter table questions drop constraint questions_question_type_check;
alter table questions add constraint questions_question_type_check check (question_type in (
  'matching_features', 'matching_headings', 'matching_information', 'sentence_endings',
  'multiple_choice', 'multiple_choice_multi', 'tfng', 'ynng', 'table_completion', 'note_completion',
  'summary_completion', 'sentence_completion', 'short_answer', 'diagram_label'
));

-- Same allow-list as 0005, plus the typed content. Still no key, no grading, no teacher data.
create or replace function app.attempt_room(p_attempt uuid, p_now timestamptz)
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
    'authoring', v_test.authoring,
    'durationMinutes', v_test.duration_minutes,
    'pageCount', v_pages,
    'questionCount', (select count(*) from questions where test_id = v_test.id),
    'mode', v_att.mode,
    'status', v_att.status,
    'deadlineAt', v_att.deadline_at,
    'serverNow', p_now,
    'sections', coalesce((select jsonb_agg(jsonb_build_object(
        'position', s.position, 'title', s.title, 'body', s.body,
        'passagePages', s.passage_pages, 'questionPages', s.question_pages,
        'numbers', (select coalesce(jsonb_agg(q.number order by q.number), '[]'::jsonb)
                    from questions q where q.section_id = s.id)) order by s.position)
      from sections s where s.test_id = v_test.id), '[]'),
    'questions', coalesce((select jsonb_agg(jsonb_build_object(
        'number', q.number, 'type', q.question_type, 'wordLimit', q.word_limit, 'group', q.answer_group,
        'prompt', q.prompt, 'options', to_jsonb(q.options))
        order by q.number) from questions q where q.test_id = v_test.id), '[]'),
    'answers', coalesce((select jsonb_agg(jsonb_build_object(
        'number', x.question_number, 'value', x.value, 'marked', x.marked_for_review, 'seq', x.seq)
        order by x.question_number) from attempt_answers x where x.attempt_id = p_attempt), '[]')
  );
end $$;
