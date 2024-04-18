/* -*- Mode: C; tab-width: 8; indent-tabs-mode: nil; c-basic-offset: 8 -*-
 *
 * Copyright (C) 2009-2010 Red Hat, Inc.
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software
 * Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA
 *
 * Written by: Matthias Clasen <mclasen@redhat.com>
 */

#pragma once

#include <glib.h>

void sys_log (GDBusMethodInvocation *context,
              const gchar           *format,
              ...) G_GNUC_PRINTF (2, 3);

gboolean get_caller_uid (GDBusMethodInvocation *context,
                         gint                  *uid);

gboolean spawn_sync (const gchar *argv[],
                     GError     **error);

gboolean get_admin_groups (gid_t  *admin_gid_out,
                           gid_t **groups_out,
                           gsize  *n_groups_out);

gint get_user_groups (const gchar *username,
                      gid_t        group,
                      gid_t      **groups);

gboolean verify_xpg_locale (const char *locale);
gboolean verify_locale (const char *locale);

void init_dirs (void);
void free_dirs (void);
const char *get_userdir (void);
const char *get_sysconfdir (void);
const char *get_icondir (void);

gboolean compat_check_exit_status (int      estatus,
                                   GError **error);
