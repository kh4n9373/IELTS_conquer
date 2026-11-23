-- A file reaches OCR only after a recorded teacher action on that exact stored file.
-- Replacing a file inserts a new asset row, so approval is never inherited.

alter table test_assets
  add column ocr_approved_at timestamptz,
  add column ocr_approved_by uuid references users (id),
  add constraint test_assets_ocr_approval_pair
    check ((ocr_approved_at is null) = (ocr_approved_by is null)),
  add constraint test_assets_ocr_approved_by_owner
    check (ocr_approved_by is null or ocr_approved_by = teacher_id);

-- Same signature as 0003, so its grants stay. Rows on an unapproved asset or an archived test are
-- never claimed, including rows that were already queued or leased before this migration.
create or replace function app.ocr_claim(p_now timestamptz, p_lease_until timestamptz)
returns table (id uuid, asset_id uuid, teacher_id uuid, attempts int, provider_job_id text)
language plpgsql security definer set search_path = public, pg_temp as $$
#variable_conflict use_column
begin
  -- A worker that died on its last attempt leaves a lease nobody may renew.
  update ocr_jobs j
  set state = 'failed', error_code = 'lease_expired', lease_until = null, finished_at = p_now
  where j.id in (
    select c.id from ocr_jobs c
    where c.state = 'running' and c.lease_until < p_now and c.attempts >= 3
    for update skip locked
  );

  return query
  update ocr_jobs j
  set state = 'running', attempts = j.attempts + 1, lease_until = p_lease_until
  where j.id = (
    select c.id from ocr_jobs c
    join test_assets a on a.id = c.asset_id
    join tests t on t.id = a.test_id
    where c.attempts < 3
      and a.ocr_approved_at is not null
      and t.status <> 'archived'
      and ((c.state = 'queued' and (c.lease_until is null or c.lease_until <= p_now))
        or (c.state = 'running' and c.lease_until < p_now))
    order by c.created_at
    limit 1
    for update of c skip locked
  )
  returning j.id, j.asset_id, j.teacher_id, j.attempts, j.provider_job_id;
end $$;
