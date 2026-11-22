-- SECURITY DEFINER functions are the only path to credentials and pre-login writes.
-- Each pins search_path, and execute is granted explicitly below.

create function app.login_lookup(p_email text)
returns table (id uuid, password_hash text, status text)
language sql stable security definer set search_path = public, pg_temp as $$
  select u.id, u.password_hash, u.status from users u where lower(u.email) = lower(trim(p_email))
$$;

create function app.session_create(p_user uuid, p_token_hash text, p_expires timestamptz)
returns void language sql security definer set search_path = public, pg_temp as $$
  insert into sessions (user_id, token_hash, expires_at) values (p_user, p_token_hash, p_expires)
$$;

create function app.session_identity(p_token_hash text, p_now timestamptz)
returns table (user_id uuid, role text, teacher_id uuid)
language sql stable security definer set search_path = public, pg_temp as $$
  select u.id, u.role, case u.role when 'teacher' then u.id when 'student' then u.teacher_id end
  from sessions s join users u on u.id = s.user_id
  where s.token_hash = p_token_hash
    and s.revoked_at is null
    and s.expires_at > p_now
    and u.status = 'active'
$$;

create function app.session_revoke(p_token_hash text, p_now timestamptz)
returns void language sql security definer set search_path = public, pg_temp as $$
  update sessions set revoked_at = p_now where token_hash = p_token_hash and revoked_at is null
$$;

create function app.reset_create(p_email text, p_token_hash text, p_now timestamptz, p_expires timestamptz)
returns table (email text)
language plpgsql security definer set search_path = public, pg_temp as $$
declare v_user users%rowtype;
begin
  select * into v_user from users u where lower(u.email) = lower(trim(p_email)) and u.status = 'active';
  if not found then return; end if;
  update password_resets set used_at = p_now where user_id = v_user.id and used_at is null;
  insert into password_resets (token_hash, user_id, expires_at) values (p_token_hash, v_user.id, p_expires);
  email := v_user.email;
  return next;
end $$;

create function app.reset_consume(p_token_hash text, p_password_hash text, p_now timestamptz)
returns boolean language plpgsql security definer set search_path = public, pg_temp as $$
declare v_user uuid;
begin
  update password_resets set used_at = p_now
  where token_hash = p_token_hash and used_at is null and expires_at > p_now
  returning user_id into v_user;
  if v_user is null then return false; end if;
  update users set password_hash = p_password_hash where id = v_user;
  update sessions set revoked_at = p_now where user_id = v_user and revoked_at is null;
  return true;
end $$;

create function app.admin_create_teacher(p_email text, p_password_hash text, p_name text)
returns uuid language plpgsql security definer set search_path = public, pg_temp as $$
declare v_id uuid;
begin
  insert into users (email, password_hash, display_name, role)
  values (lower(trim(p_email)), p_password_hash, p_name, 'teacher')
  returning id into v_id;
  return v_id;
exception when unique_violation then
  raise exception 'email_taken';
end $$;

create function app.invite_preview(p_code text, p_now timestamptz)
returns table (class_name text)
language sql stable security definer set search_path = public, pg_temp as $$
  select c.name from invites i join classes c on c.id = i.class_id
  where i.code = upper(trim(p_code))
    and i.disabled_at is null and i.expires_at > p_now and c.status = 'active'
$$;

create function app.register_student(
  p_code text, p_email text, p_password_hash text, p_name text,
  p_ai_consent boolean, p_terms_version text, p_now timestamptz
) returns uuid language plpgsql security definer set search_path = public, pg_temp as $$
declare v_invite invites%rowtype; v_id uuid;
begin
  select i.* into v_invite from invites i join classes c on c.id = i.class_id
  where i.code = upper(trim(p_code))
    and i.disabled_at is null and i.expires_at > p_now and c.status = 'active';
  if not found then raise exception 'invite_invalid'; end if;

  begin
    insert into users (email, password_hash, display_name, role, teacher_id, age_confirmed_at)
    values (lower(trim(p_email)), p_password_hash, p_name, 'student', v_invite.teacher_id, p_now)
    returning id into v_id;
  exception when unique_violation then
    raise exception 'email_taken';
  end;

  insert into class_members (class_id, teacher_id, student_id)
  values (v_invite.class_id, v_invite.teacher_id, v_id);
  insert into consents (user_id, purpose, terms_version, granted) values
    (v_id, 'core_learning', p_terms_version, true),
    (v_id, 'ai_processing', p_terms_version, p_ai_consent);
  return v_id;
end $$;

create function app.join_class(p_code text, p_now timestamptz)
returns void language plpgsql security definer set search_path = public, pg_temp as $$
declare v_invite invites%rowtype; v_student users%rowtype;
begin
  select * into v_student from users where id = app.user_id() and role = 'student' and status = 'active';
  if not found then raise exception 'invite_invalid'; end if;

  select i.* into v_invite from invites i join classes c on c.id = i.class_id
  where i.code = upper(trim(p_code))
    and i.disabled_at is null and i.expires_at > p_now and c.status = 'active';
  if not found then raise exception 'invite_invalid'; end if;
  if v_invite.teacher_id <> v_student.teacher_id then raise exception 'other_teacher'; end if;

  insert into class_members (class_id, teacher_id, student_id)
  values (v_invite.class_id, v_invite.teacher_id, v_student.id)
  on conflict (class_id, student_id) do update set removed_at = null, joined_at = p_now
    where class_members.removed_at is not null;
end $$;

revoke execute on all functions in schema app from public;
grant execute on function app.user_id(), app.teacher_id() to npa_app, npa_student, npa_teacher, npa_admin;
grant execute on function
  app.login_lookup(text),
  app.session_create(uuid, text, timestamptz),
  app.session_identity(text, timestamptz),
  app.session_revoke(text, timestamptz),
  app.reset_create(text, text, timestamptz, timestamptz),
  app.reset_consume(text, text, timestamptz),
  app.invite_preview(text, timestamptz),
  app.register_student(text, text, text, text, boolean, text, timestamptz)
  to npa_app;
grant execute on function app.admin_create_teacher(text, text, text) to npa_admin;
grant execute on function app.join_class(text, timestamptz) to npa_student;
