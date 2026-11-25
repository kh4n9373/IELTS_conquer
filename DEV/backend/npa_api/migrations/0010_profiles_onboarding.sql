-- Profiles and Google onboarding: a verified Google identity waits in a short-lived
-- signup ticket while the person picks a role. Self-registered teachers start 'pending'
-- until an admin approves them; session_identity already refuses non-active users.

alter table users drop constraint users_status_check;
alter table users add constraint users_status_check check (status in ('active', 'locked', 'pending'));

alter table users
  add column avatar_url text check (avatar_url is null or (avatar_url ~ '^https://' and length(avatar_url) <= 2048)),
  add column phone text check (phone is null or phone ~ '^\+?[0-9 .-]{8,20}$'),
  add column referral_source text check (
    referral_source in ('facebook', 'tiktok', 'friend', 'teacher', 'search', 'other')
  ),
  add column current_band numeric(2, 1) check (current_band between 0 and 9 and current_band * 2 = trunc(current_band * 2)),
  add column target_band numeric(2, 1) check (target_band between 0 and 9 and target_band * 2 = trunc(target_band * 2)),
  add column auth_provider text not null default 'password' check (auth_provider in ('password', 'google'));

grant select (avatar_url, phone, referral_source, current_band, target_band, auth_provider)
  on users to npa_student, npa_teacher, npa_admin;

create table signup_tickets (
  token_hash text primary key,
  email text not null,
  name text not null,
  avatar_url text,
  expires_at timestamptz not null,
  used_at timestamptz,
  created_at timestamptz not null default now()
);
alter table signup_tickets enable row level security;

create function app.signup_ticket_create(
  p_token_hash text, p_email text, p_name text, p_avatar text, p_expires timestamptz
) returns void language sql security definer set search_path = public, pg_temp as $$
  insert into signup_tickets (token_hash, email, name, avatar_url, expires_at)
  values (p_token_hash, lower(trim(p_email)), p_name, p_avatar, p_expires)
$$;

-- Consumes the ticket; callers run it in the same transaction as the insert, so a failed
-- signup (bad invite code, taken email) rolls the consumption back and the ticket stays usable.
-- Google accounts get a random password hash from the API; the reset flow can set a real one.
create function app.signup_ticket_take(p_token_hash text, p_now timestamptz)
returns signup_tickets language plpgsql security definer set search_path = public, pg_temp as $$
declare v signup_tickets%rowtype;
begin
  update signup_tickets set used_at = p_now
  where token_hash = p_token_hash and used_at is null and expires_at > p_now
  returning * into v;
  if not found then raise exception 'ticket_invalid'; end if;
  return v;
end $$;

create function app.onboard_student(
  p_ticket text, p_code text, p_password_hash text, p_name text, p_phone text, p_referral text,
  p_current numeric, p_target numeric, p_ai_consent boolean, p_terms_version text, p_now timestamptz
) returns uuid language plpgsql security definer set search_path = public, pg_temp as $$
declare v_ticket signup_tickets%rowtype; v_id uuid;
begin
  v_ticket := app.signup_ticket_take(p_ticket, p_now);
  v_id := app.register_student(
    p_code, v_ticket.email, p_password_hash, p_name, p_ai_consent, p_terms_version, p_now
  );
  update users set
    avatar_url = v_ticket.avatar_url, phone = p_phone, referral_source = p_referral,
    current_band = p_current, target_band = p_target, auth_provider = 'google'
  where id = v_id;
  return v_id;
end $$;

create function app.onboard_teacher(
  p_ticket text, p_password_hash text, p_name text, p_phone text, p_referral text, p_now timestamptz
) returns uuid language plpgsql security definer set search_path = public, pg_temp as $$
declare v_ticket signup_tickets%rowtype; v_id uuid;
begin
  v_ticket := app.signup_ticket_take(p_ticket, p_now);
  begin
    insert into users (
      email, password_hash, display_name, role, status,
      avatar_url, phone, referral_source, auth_provider
    ) values (
      v_ticket.email, p_password_hash, p_name, 'teacher', 'pending',
      v_ticket.avatar_url, p_phone, p_referral, 'google'
    ) returning id into v_id;
  exception when unique_violation then
    raise exception 'email_taken';
  end;
  return v_id;
end $$;

-- Google is the source of truth for the avatar; the name stays whatever the user set.
create function app.google_touch(p_user uuid, p_avatar text)
returns void language sql security definer set search_path = public, pg_temp as $$
  update users set avatar_url = coalesce(p_avatar, avatar_url) where id = p_user
$$;

create function app.update_profile(p_name text, p_phone text, p_current numeric, p_target numeric)
returns void language sql security definer set search_path = public, pg_temp as $$
  update users set display_name = p_name, phone = p_phone, current_band = p_current, target_band = p_target
  where id = app.user_id()
$$;

revoke execute on all functions in schema app from public;
grant execute on function app.user_id(), app.teacher_id() to npa_app, npa_student, npa_teacher, npa_admin;
grant execute on function
  app.signup_ticket_create(text, text, text, text, timestamptz),
  app.onboard_student(text, text, text, text, text, text, numeric, numeric, boolean, text, timestamptz),
  app.onboard_teacher(text, text, text, text, text, timestamptz),
  app.google_touch(uuid, text)
  to npa_app;
grant execute on function app.update_profile(text, text, numeric, numeric)
  to npa_student, npa_teacher, npa_admin;
