-- Schema import (panxg-exam/v1): an AI-filled JSON becomes a manual test. Its text lands in the manual
-- columns (sections.body, questions.prompt and options); its answers land in the key with origin 'import'.
alter table keys.answer_key_items drop constraint answer_key_items_origin_check;
alter table keys.answer_key_items add constraint answer_key_items_origin_check
  check (origin in ('manual', 'text_layer', 'scan', 'import'));
-- Why the AI was unsure. Teacher-only, like the rest of the keys schema.
alter table keys.answer_key_items add column note text check (length(note) <= 2000);
