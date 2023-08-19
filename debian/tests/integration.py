#!/usr/bin/python3
#
# Destructive integration test for accountsservice, to be run in an
# expendable chroot, container or VM.
#
# Copyright 2023 Simon McVittie
# SPDX-License-Identifier: GPL-3.0-or-later

import ctypes
import json
import os
import pwd
import random
import shutil
import subprocess
import time
import unittest
import uuid
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import gi
gi.require_version('AccountsService', '1.0')

from gi.repository import AccountsService as Act        # noqa
from gi.repository import GLib                          # noqa


LOG = '/run/accountsservice-integration-test.log'

SHELLS: set[str] = set()

for line in open('/etc/shells', 'r'):
    SHELLS.add(line.rstrip('\n'))


# Reimplement deprecated spwd module
Spwd = namedtuple(
    'Spwd',
    [
        'sp_nam', 'sp_pwd', 'sp_lstchg', 'sp_min', 'sp_max', 'sp_warn',
        'sp_inact', 'sp_expire', 'sp_flag',
    ],
)

# Reimplement deprecated crypt module
libcrypt = ctypes.cdll.LoadLibrary('libcrypt.so.1')
c_crypt = libcrypt.crypt
c_crypt.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
c_crypt.restype = ctypes.c_char_p


def crypt(word: str, salt: str) -> str:
    return c_crypt(word.encode('utf-8'), salt.encode('utf-8')).decode('utf-8')


class AccountsServiceTest(unittest.TestCase):
    def get_shadow(self, username: str) -> Spwd:
        with open('/etc/shadow', 'r') as reader:
            for line in reader:
                fields = line.rstrip('\n').split(':')

                if fields[0] == username:
                    assert len(fields) == 9, fields

                    return Spwd(
                        fields[0],              # nam
                        fields[1],              # pwd
                        int(fields[2]),         # lstchg
                        int(fields[3] or '0'),  # min
                        int(fields[4] or '0'),  # max
                        int(fields[5] or '0'),  # warn
                        int(fields[6] or '0'),  # inact
                        int(fields[7] or '0'),  # expire
                        int(fields[8] or '0'),  # flag
                    )

        raise KeyError(f'{username} not found in /etc/shadow')

    def describe_pwent(self, caption: str, pwent: Any) -> None:
        print(f'{caption}: {pwent!r}')

    def describe_shadow(self, caption: str, shadow: Spwd) -> None:
        print(f'{caption}: {shadow!r}')

    def describe_act_user(self, caption: str, user: Act.User) -> None:
        print(f'{caption}: {user!r}')
        print(f'\taccount type: {user.get_account_type()!r}')
        print(f'\tautomatic login: {user.get_automatic_login()!r}')
        print(f'\temail: {user.get_email()!r}')
        print(f'\thome: {user.get_home_dir()!r}')
        print(f'\ticon file: {user.get_icon_file()!r}')
        print(f'\tis loaded: {user.is_loaded()!r}')
        print(f'\tis local account: {user.is_local_account()!r}')
        print(f'\tis logged in anywhere: {user.is_logged_in_anywhere()!r}')
        print(f'\tis logged in: {user.is_logged_in()!r}')
        print(f'\tis nonexistent: {user.is_nonexistent()!r}')
        print(f'\tis system account: {user.is_system_account()!r}')
        print(f'\tlanguage: {user.get_language()!r}')
        print(f'\tlocation: {user.get_location()!r}')
        print(f'\tlocked: {user.get_locked()!r}')
        print(f'\tlogin frequency: {user.get_login_frequency()!r}')
        print(f'\tlogin time: {user.get_login_time()!r}')
        print(
            '\tnumber of sessions anywhere: '
            f'{user.get_num_sessions_anywhere()!r}'
        )
        print(f'\tnumber of sessions: {user.get_num_sessions()!r}')
        print(f'\tpassword hint: {user.get_password_hint()!r}')
        print(f'\tpassword mode: {user.get_password_mode()!r}')
        print(f'\tprimary session ID: {user.get_primary_session_id()!r}')
        print(f'\treal name: {user.get_real_name()!r}')
        print(f'\tsaved: {user.get_saved()!r}')
        print(f'\tsession type: {user.get_session_type()!r}')
        print(f'\tsession: {user.get_session()!r}')
        print(f'\tshell: {user.get_shell()!r}')
        print(f'\tuid: {user.get_uid()!r}')
        print(f'\tusername: {user.get_user_name()!r}')
        print(f'\tX session: {user.get_x_session()!r}')

    def test_library_api(self) -> None:
        start_time = time.monotonic()
        TIMEOUT = 60.0          # arbitrary timeout in seconds

        # Wake up loops at least once per 10 seconds
        timeout_id = GLib.timeout_add_seconds(10, lambda *anything: True)

        with open(LOG, 'w') as writer:
            os.chmod(writer.fileno(), 0o600)

        epoch = datetime.fromtimestamp(0, timezone.utc)
        days_since_epoch = (datetime.now(timezone.utc) - epoch).days

        manager = Act.UserManager.get_default()
        start_time = time.monotonic()

        while (
            (time.monotonic() - start_time) < TIMEOUT
            and not manager.props.is_loaded
        ):
            GLib.MainContext.default().iteration(True)

        self.assertTrue(manager.props.is_loaded)

        # If a user has previously been cached, it is marked as being not a
        # system user (see
        # https://gitlab.freedesktop.org/accountsservice/accountsservice/-/issues/108
        # for rationale)
        manager.uncache_user(SYSTEM_USER)

        sysuser = manager.get_user(SYSTEM_USER)
        start_time = time.monotonic()

        while (
            (time.monotonic() - start_time) < TIMEOUT
            and not sysuser.props.is_loaded
        ):
            GLib.MainContext.default().iteration(True)

        self.describe_act_user('system', sysuser)
        self.assertTrue(sysuser.props.is_loaded)
        self.assertEqual(sysuser.is_system_account(), True)
        self.assertEqual(sysuser.is_local_account(), True)

        remote_user = manager.cache_user(REMOTE_USER)
        start_time = time.monotonic()

        while (
            (time.monotonic() - start_time) < TIMEOUT
            and not remote_user.props.is_loaded
        ):
            GLib.MainContext.default().iteration(True)

        self.describe_act_user('remote', remote_user)
        self.assertTrue(remote_user.props.is_loaded)
        self.assertEqual(remote_user.is_system_account(), False)
        self.assertEqual(remote_user.is_local_account(), False)

        user = manager.create_user(
            LOCAL_USER,
            'Aaron A. Aaronson',
            Act.UserAccountType.STANDARD,
        )
        start_time = time.monotonic()

        while (
            (time.monotonic() - start_time) < TIMEOUT
            and not user.props.is_loaded
        ):
            GLib.MainContext.default().iteration(True)

        self.describe_act_user('new', user)
        pwent = pwd.getpwnam(LOCAL_USER)
        self.describe_pwent('new', pwent)
        shadow = self.get_shadow(LOCAL_USER)
        self.describe_shadow('new', shadow)

        self.assertTrue(user.props.is_loaded)

        # https://bugs.debian.org/1030253
        self.assertIn(pwent.pw_shell, SHELLS)

        self.assertEqual(user.get_uid(), pwent.pw_uid)
        self.assertEqual(user.get_user_name(), LOCAL_USER)
        self.assertEqual(user.get_real_name(), 'Aaron A. Aaronson')
        self.assertEqual(user.get_account_type(), Act.UserAccountType.STANDARD)
        self.assertEqual(user.get_password_hint(), '')
        self.assertEqual(
            user.get_password_mode(),
            Act.UserPasswordMode.REGULAR,
        )
        self.assertEqual(user.get_home_dir(), pwent.pw_dir)
        self.assertEqual(user.get_shell(), pwent.pw_shell)
        self.assertEqual(user.get_email(), '')
        self.assertEqual(user.get_location(), '')
        self.assertEqual(user.get_num_sessions(), 0)
        self.assertEqual(user.get_num_sessions_anywhere(), 0)
        self.assertEqual(user.is_logged_in(), False)
        self.assertEqual(user.is_logged_in_anywhere(), False)
        self.assertEqual(user.get_login_frequency(), 0)
        self.assertEqual(user.get_login_time(), 0)
        self.assertEqual(user.get_saved(), True)
        self.assertEqual(user.get_locked(), True)
        self.assertEqual(user.get_automatic_login(), False)
        self.assertEqual(user.is_system_account(), False)
        # https://bugs.debian.org/1030262
        self.assertEqual(user.is_local_account(), True)
        self.assertEqual(user.is_nonexistent(), False)
        self.assertEqual(user.get_primary_session_id(), None)
        self.assertEqual(user.is_loaded(), True)

        if False:
            # These are currently true but seem like they could reasonably
            # have a non-trivial default in a future version
            self.assertEqual(user.get_language(), '')
            self.assertEqual(user.get_x_session(), '')
            self.assertEqual(user.get_session(), '')
            self.assertEqual(user.get_session_type(), '')

        self.assertEqual(shadow.sp_lstchg, days_since_epoch)

        with self.subTest('metadata'):
            user.set_email('aaron@example.com')
            user.set_language('en_GB.utf8')
            user.set_x_session('gnome')
            user.set_session('gnome')
            user.set_session_type('wayland')
            user.set_location('Sandford')
            user.set_real_name('Aaron Aaronson')

            user2 = manager.get_user_by_id(pwent.pw_uid)
            start_time = time.monotonic()

            while (
                (time.monotonic() - start_time) < TIMEOUT
                and not user2.props.is_loaded
            ):
                GLib.MainContext.default().iteration(True)

            # It doesn't update synchronously...
            while (
                (time.monotonic() - start_time) < TIMEOUT
                and user2.props.real_name != 'Aaron Aaronson'
            ):
                GLib.MainContext.default().iteration(True)

            self.describe_act_user('modified', user2)
            pwent = pwd.getpwnam(LOCAL_USER)
            self.describe_pwent('modified', pwent)
            shadow = self.get_shadow(LOCAL_USER)
            self.describe_shadow('modified', shadow)

            self.assertTrue(user2.props.is_loaded)
            self.assertEqual(user2.props.real_name, 'Aaron Aaronson')
            self.assertEqual(user2.get_email(), 'aaron@example.com')
            self.assertEqual(user2.get_language(), 'en_GB.utf8')
            self.assertEqual(user2.get_location(), 'Sandford')
            self.assertEqual(user2.get_session(), 'gnome')
            self.assertEqual(user2.get_session_type(), 'wayland')
            self.assertEqual(user2.get_x_session(), 'gnome')
            self.assertEqual(user2.get_real_name(), 'Aaron Aaronson')

        with self.subTest('passwordless'):
            user.set_password_mode(Act.UserPasswordMode.NONE)
            start_time = time.monotonic()

            while (
                (time.monotonic() - start_time) < TIMEOUT
                and user2.get_password_mode() != Act.UserPasswordMode.NONE
            ):
                GLib.MainContext.default().iteration(True)

            self.describe_act_user('after set passwordless', user)
            pwent = pwd.getpwnam(LOCAL_USER)
            self.describe_pwent('after set passwordless', pwent)
            shadow = self.get_shadow(LOCAL_USER)
            self.describe_shadow('after set passwordless', shadow)

            self.assertEqual(
                user2.get_password_mode(),
                Act.UserPasswordMode.NONE,
            )
            self.assertEqual(shadow.sp_pwd, '')
            self.assertEqual(shadow.sp_lstchg, days_since_epoch)

        with self.subTest('set password'):
            password = uuid.uuid4().hex.replace('-', '')
            user.set_password(password, 'you know the one')
            start_time = time.monotonic()

            while (
                (time.monotonic() - start_time) < TIMEOUT
                and user2.props.password_hint != 'you know the one'
            ):
                GLib.MainContext.default().iteration(True)

            self.describe_act_user('after setting password', user)
            pwent = pwd.getpwnam(LOCAL_USER)
            self.describe_pwent('after setting password', pwent)
            shadow = self.get_shadow(LOCAL_USER)
            self.describe_shadow('after setting password', shadow)
            self.assertEqual(user2.get_password_hint(), 'you know the one')
            self.assertEqual(crypt(password, shadow.sp_pwd), shadow.sp_pwd)
            self.assertEqual(user2.get_locked(), False)

            user.set_password_hint('whatever')
            start_time = time.monotonic()

            while (
                (time.monotonic() - start_time) < TIMEOUT
                and user2.props.password_hint != 'whatever'
            ):
                GLib.MainContext.default().iteration(True)

            shadow2 = self.get_shadow(LOCAL_USER)
            self.assertEqual(shadow2, shadow)
            self.assertEqual(user2.get_password_hint(), 'whatever')

        with self.subTest('lock'):
            user.set_locked(True)
            start_time = time.monotonic()

            while (
                (time.monotonic() - start_time) < TIMEOUT
                and not user2.get_locked()
            ):
                GLib.MainContext.default().iteration(True)

            self.describe_act_user('after locking', user)
            pwent = pwd.getpwnam(LOCAL_USER)
            self.describe_pwent('after locking', pwent)
            shadow = self.get_shadow(LOCAL_USER)
            self.describe_shadow('after locking', shadow)

            self.assertNotEqual(crypt(password, shadow.sp_pwd), shadow.sp_pwd)
            self.assertEqual(user2.get_locked(), True)

        with self.subTest('unlock'):
            user.set_locked(False)
            start_time = time.monotonic()

            while (
                (time.monotonic() - start_time) < TIMEOUT
                and user2.get_locked()
            ):
                GLib.MainContext.default().iteration(True)

            self.describe_act_user('after unlocking', user)
            pwent = pwd.getpwnam(LOCAL_USER)
            self.describe_pwent('after unlocking', pwent)
            shadow = self.get_shadow(LOCAL_USER)
            self.describe_shadow('after unlocking', shadow)

            self.assertEqual(crypt(password, shadow.sp_pwd), shadow.sp_pwd)
            self.assertEqual(user2.get_locked(), False)

        with self.subTest('set at login'):
            user.set_password_mode(Act.UserPasswordMode.SET_AT_LOGIN)
            start_time = time.monotonic()

            while (
                (time.monotonic() - start_time) < TIMEOUT
                and (
                    user2.get_password_mode()
                    != Act.UserPasswordMode.SET_AT_LOGIN
                )
            ):
                GLib.MainContext.default().iteration(True)

            self.describe_act_user('after set-at-login', user)
            pwent = pwd.getpwnam(LOCAL_USER)
            self.describe_pwent('after set-at-login', pwent)
            shadow = self.get_shadow(LOCAL_USER)
            self.describe_shadow('after set-at-login', shadow)

            self.assertEqual(
                user2.get_password_mode(),
                Act.UserPasswordMode.SET_AT_LOGIN,
            )
            self.assertEqual(shadow.sp_pwd, '')
            # "The value 0 has a special meaning, which is that the user
            # should change her password the next time she will log in the
            # system." —shadow(5)
            self.assertEqual(shadow.sp_lstchg, 0)

        # not exercising set_automatic_login: would require a display manager

        # TODO: test user.get_icon_file()

        self.assertTrue(Path(pwent.pw_dir).exists())
        manager.delete_user(
            user,
            True,   # delete files
        )
        self.assertFalse(Path(pwent.pw_dir).exists())

        with self.subTest('CVE-2012-6655'):
            with open(LOG, 'r') as reader:
                for line in reader:
                    print(f'from log: {line!r}')
                    self.assertNotIn('CVE-2012-6655', line)

        GLib.source_remove(timeout_id)


# Use randomly-generated usernames to avoid collisions. adduser limits
# usernames to 32 characters, so use a 2-character prefix plus 30 out
# of the 32 in the UUID.
LOCAL_USER = 'l-' + uuid.uuid4().hex.replace('-', '')[:30]
REMOTE_USER = 'r-' + uuid.uuid4().hex.replace('-', '')[:30]
SYSTEM_USER = '_apt'
REMOTE_UID = -1


def main() -> None:
    global REMOTE_UID

    try:
        pwd.getpwnam(LOCAL_USER)
    except KeyError:
        pass
    else:
        raise AssertionError(f'{LOCAL_USER} should not already exist')

    try:
        pwd.getpwnam(REMOTE_USER)
    except KeyError:
        pass
    else:
        raise AssertionError(f'{REMOTE_USER} should not already exist')

    while True:
        # Choose a random unused uid in the middle of the range for
        # ordinary human users
        REMOTE_UID = random.randint(2000, 49999)

        try:
            pwd.getpwuid(REMOTE_UID)
        except KeyError:
            # Define a user that will be treated as remotely-managed,
            # using nss-systemd(8).
            # TODO: One day accountsservice should treat systemd accounts
            # as local, at which point we will have to do this some other way.
            Path('/run/userdb').mkdir(mode=0o755, exist_ok=True)

            with open(f'/run/userdb/{REMOTE_USER}.user', 'w') as writer:
                os.chmod(writer.fileno(), 0o644)
                json.dump(
                    {
                        'userName': REMOTE_USER,
                        'uid': REMOTE_UID,
                        'realName': 'Nicholas Angel',
                        'disposition': 'regular',
                        'homeDirectory': f'/home/{REMOTE_USER}',
                        'emailAddress': 'angel.nicholas@met.example',
                        'location': 'London',
                        'shell': '/bin/bash',
                    },
                    writer,
                )

            with open(
                f'/run/userdb/{REMOTE_USER}.user-privileged', 'w',
            ) as writer:
                os.chmod(writer.fileno(), 0o600)
                json.dump(
                    {
                        'passwordHint': 'Uncle who bought me a pedal car',
                    },
                    writer,
                )

            Path(f'/run/userdb/{REMOTE_UID}.user').symlink_to(
                f'{REMOTE_USER}.user'
            )
            Path(f'/run/userdb/{REMOTE_UID}.user-privileged').symlink_to(
                f'{REMOTE_USER}.user-privileged'
            )

            break

    subprocess.run(
        ['dpkg-divert', '--rename', '--add', '/usr/sbin/usermod'],
        check=True,
    )
    subprocess.run(
        ['dpkg-divert', '--rename', '--add', '/usr/sbin/chpasswd'],
        check=True,
    )
    shutil.copyfile('debian/tests/chpasswd', '/usr/sbin/chpasswd')
    os.chmod('/usr/sbin/chpasswd', 0o755)
    shutil.copyfile('debian/tests/usermod', '/usr/sbin/usermod')
    os.chmod('/usr/sbin/usermod', 0o755)

    unittest.main(verbosity=2)


if __name__ == '__main__':
    main()
