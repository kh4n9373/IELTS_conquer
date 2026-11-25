-- First-run profile setup: every account created from now on walks through the setup screens
-- once (students: seven, teachers: one). Accounts that already exist count as done.
-- The uploaded profile photo is a small JPEG kept in its own table and read only through
-- security-definer functions, so it never shows up in a plain `select * from users`.

alter table users drop constraint users_referral_source_check;
alter table users add constraint users_referral_source_check check (
  referral_source in ('facebook', 'tiktok', 'instagram', 'youtube', 'friend', 'teacher', 'search', 'other')
);

alter table users
  add column age smallint check (age between 18 and 100),
  add column occupation text check (occupation in ('school', 'university', 'working', 'other')),
  add column cefr_level text check (cefr_level in ('A1', 'A2', 'B1', 'B2', 'C1', 'C2')),
  add column skill_rank text[] check (
    skill_rank is null or (
      cardinality(skill_rank) = 4
      and skill_rank @> array['listening', 'reading', 'writing', 'speaking']
    )
  ),
  add column study_minutes smallint check (study_minutes in (10, 20, 30, 45, 60)),
  add column study_time text check (study_time in ('morning', 'noon', 'evening', 'night')),
  add column study_styles text[] check (
    study_styles is null or study_styles <@ array['video', 'exercise', 'flashcard', 'tutor']
  ),
  add column reminders boolean,
  add column photo_updated_at timestamptz,
  add column onboarded_at timestamptz;

update users set onboarded_at = created_at;

grant select (
  age, occupation, cefr_level, skill_rank, study_minutes, study_time, study_styles, reminders,
  photo_updated_at, onboarded_at
) on users to npa_student, npa_teacher, npa_admin;

create table user_photos (
  user_id uuid primary key references users (id) on delete cascade,
  jpeg bytea not null check (octet_length(jpeg) between 100 and 307200),
  updated_at timestamptz not null
);
alter table user_photos enable row level security;

create function app.set_photo(p_jpeg bytea, p_now timestamptz)
returns void language sql security definer set search_path = public, pg_temp as $$
  insert into user_photos (user_id, jpeg, updated_at) values (app.user_id(), p_jpeg, p_now)
  on conflict (user_id) do update set jpeg = excluded.jpeg, updated_at = excluded.updated_at;
  update users set photo_updated_at = p_now where id = app.user_id();
$$;

create function app.my_photo()
returns bytea language sql stable security definer set search_path = public, pg_temp as $$
  select jpeg from user_photos where user_id = app.user_id()
$$;

-- Teachers answer only the name and age; the student-only arguments arrive as nulls.
create function app.complete_setup(
  p_name text, p_age smallint, p_occupation text, p_referral text, p_target numeric,
  p_cefr text, p_skills text[], p_minutes smallint, p_time text, p_styles text[],
  p_reminders boolean, p_now timestamptz
) returns void language plpgsql security definer set search_path = public, pg_temp as $$
begin
  update users set
    display_name = p_name, age = p_age,
    occupation = coalesce(p_occupation, occupation),
    referral_source = coalesce(p_referral, referral_source),
    target_band = coalesce(p_target, target_band),
    cefr_level = coalesce(p_cefr, cefr_level),
    skill_rank = coalesce(p_skills, skill_rank),
    study_minutes = coalesce(p_minutes, study_minutes),
    study_time = coalesce(p_time, study_time),
    study_styles = coalesce(p_styles, study_styles),
    reminders = coalesce(p_reminders, reminders),
    onboarded_at = coalesce(onboarded_at, p_now)
  where id = app.user_id();
end $$;

revoke execute on function app.set_photo(bytea, timestamptz), app.my_photo(),
  app.complete_setup(text, smallint, text, text, numeric, text, text[], smallint, text, text[], boolean, timestamptz)
  from public;
grant execute on function app.set_photo(bytea, timestamptz), app.my_photo(),
  app.complete_setup(text, smallint, text, text, numeric, text, text[], smallint, text, text[], boolean, timestamptz)
  to npa_student, npa_teacher, npa_admin;
