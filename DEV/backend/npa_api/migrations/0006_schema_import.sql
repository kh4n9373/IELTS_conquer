-- Schema import (panxg-exam/v1): composed tests carry their text in tests.content and need no PDF.
-- tests.content never holds answers; those stay in keys.answer_key_items with origin 'import'.
alter table tests add column authoring_mode text not null default 'pdf'
  check (authoring_mode in ('pdf', 'composed'));
alter table tests add column content jsonb;
alter table tests add constraint tests_content_mode check ((authoring_mode = 'composed') = (content is not null));

alter table keys.answer_key_items drop constraint answer_key_items_origin_check;
alter table keys.answer_key_items add constraint answer_key_items_origin_check
  check (origin in ('manual', 'text_layer', 'scan', 'import'));
-- Why the AI was unsure. Teacher-only, like the rest of the keys schema.
alter table keys.answer_key_items add column note text check (length(note) <= 2000);

-- Same allow-list as 0005, plus the student-safe text of a composed test.
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
    'authoringMode', v_test.authoring_mode,
    'content', v_test.content,
    'durationMinutes', v_test.duration_minutes,
    'pageCount', v_pages,
    'questionCount', (select count(*) from questions where test_id = v_test.id),
    'mode', v_att.mode,
    'status', v_att.status,
    'deadlineAt', v_att.deadline_at,
    'serverNow', p_now,
    'sections', coalesce((select jsonb_agg(jsonb_build_object(
        'position', s.position, 'title', s.title,
        'passagePages', s.passage_pages, 'questionPages', s.question_pages,
        'numbers', (select coalesce(jsonb_agg(q.number order by q.number), '[]'::jsonb)
                    from questions q where q.section_id = s.id)) order by s.position)
      from sections s where s.test_id = v_test.id), '[]'),
    'questions', coalesce((select jsonb_agg(jsonb_build_object(
        'number', q.number, 'type', q.question_type, 'wordLimit', q.word_limit, 'group', q.answer_group)
        order by q.number) from questions q where q.test_id = v_test.id), '[]'),
    'answers', coalesce((select jsonb_agg(jsonb_build_object(
        'number', x.question_number, 'value', x.value, 'marked', x.marked_for_review, 'seq', x.seq)
        order by x.question_number) from attempt_answers x where x.attempt_id = p_attempt), '[]')
  );
end $$;
