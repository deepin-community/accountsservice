#!/usr/bin/python3

# accountsservice daemon test
#
# Copyright: (C) 2011 Martin Pitt <martin.pitt@ubuntu.com>
# (C) 2022 Bastien Nocera <hadess@hadess.net>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

import os
import sys
import dbus
import tempfile
import shutil
import subprocess
import unittest
import time

try:
    import gi
    from gi.repository import GLib
    from gi.repository import Gio
except ImportError as e:
    sys.stderr.write('Skipping tests, PyGobject not available for Python 3, or missing GI typelibs: %s\n' % str(e))
    sys.exit(0)

try:
    import dbusmock
except ImportError:
    sys.stderr.write('Skipping tests, python-dbusmock not available (http://pypi.python.org/pypi/python-dbusmock).\n')
    sys.exit(0)

if os.geteuid() == 0 or os.getuid() == 0:
    sys.stderr.write('Skipping tests, daemon tests cannot run as root\n')
    sys.exit(77)

AD = 'org.freedesktop.Accounts'
AD_PATH = '/org/freedesktop/Accounts'
AD_USER = AD + '.User'
AD_USER_PATH = AD_PATH + '/User'

SIMULATED_SYSTEM_LOCALE = 'en_IE.UTF-8'

class Tests(dbusmock.DBusTestCase):
    @classmethod
    def setUpClass(cls):
        # run from local build tree if we are in one, otherwise use system instance
        builddir = os.getenv('top_builddir', '.')
        if os.access(os.path.join(builddir, 'src', 'accounts-daemon'), os.X_OK):
            cls.daemon_path = os.path.join(builddir, 'src', 'accounts-daemon')
            print('Testing binaries from local build tree (%s)' % cls.daemon_path)
        elif os.environ.get('UNDER_JHBUILD', False):
            jhbuild_prefix = os.environ['JHBUILD_PREFIX']
            cls.daemon_path = os.path.join(jhbuild_prefix, 'libexec', 'accounts-daemon')
            print('Testing binaries from JHBuild (%s)' % cls.daemon_path)
        else:
            cls.daemon_path = None
            with open('/usr/lib/systemd/system/accounts-daemon.service') as f:
                for line in f:
                    if line.startswith('ExecStart='):
                        cls.daemon_path = line.split('=', 1)[1].strip()
                        break
            assert cls.daemon_path, 'could not determine daemon path from systemd .service file'
            print('Testing installed system binary (%s)' % cls.daemon_path)

        assert(os.getenv('top_srcdir'))

        # fail on CRITICALs on client side
        GLib.log_set_always_fatal(GLib.LogLevelFlags.LEVEL_WARNING |
                                  GLib.LogLevelFlags.LEVEL_ERROR |
                                  GLib.LogLevelFlags.LEVEL_CRITICAL)

        # set up a fake system D-BUS
        cls.test_bus = Gio.TestDBus.new(Gio.TestDBusFlags.NONE)
        cls.test_bus.up()
        try:
            del os.environ['DBUS_SESSION_BUS_ADDRESS']
        except KeyError:
            pass
        os.environ['DBUS_SYSTEM_BUS_ADDRESS'] = cls.test_bus.get_bus_address()

        cls.dbus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        cls.dbus_con = cls.get_dbus(True)
        cls._polkitd = None

    @classmethod
    def tearDownClass(cls):
        cls.test_bus.down()
        dbusmock.DBusTestCase.tearDownClass()

    def setUp(self):
        self.proxy = None
        self.log = None
        self.daemon = None

    def run(self, result=None):
        super(Tests, self).run(result)
        if result and len(result.errors) + len(result.failures) > 0 and self.log:
            with open(self.log.name) as f:
                sys.stderr.write('\n-------------- daemon log: ----------------\n')
                sys.stderr.write(f.read())
                sys.stderr.write('------------------------------\n')

    def tearDown(self):
        self.stop_daemon()

    #
    # Daemon control and D-BUS I/O
    #

    def start_daemon(self):
        '''Start daemon and create DBus proxy.

        When done, this sets self.proxy as the Gio.DBusProxy for accounts-daemon.
        '''
        env = os.environ.copy()
        env['G_DEBUG'] = 'fatal-criticals'
        env['G_MESSAGES_DEBUG'] = 'all'

        # Set up tempdir
        self.test_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.test_dir)
        srcdir = os.getenv('top_srcdir')
        shutil.copytree(os.path.join(srcdir, 'tests', 'data', 'etc'), os.path.join(self.test_dir, 'etc'))
        shutil.copytree(os.path.join(srcdir, 'tests', 'data', 'var'), os.path.join(self.test_dir, 'var'))

        users = ['rupert', 'benedict']
        for user in users:
            path = os.path.join(self.test_dir, 'var', 'lib', 'AccountsService', 'users', user)
            with open(path + '.in') as f:
                    content = f.read()
                    content = content.replace("@ROOTDIR@", self.test_dir)
                    os.remove(path + '.in')
                    with open(path, "w") as d:
                        d.write(content)
                        d.close()

        env['ROOTDIR'] = self.test_dir
        env['LD_PRELOAD'] = os.getenv('MOCKLIBC_LD_PRELOAD')
        env['MOCK_PASSWD'] = os.path.join(self.test_dir, 'etc', 'passwd')
        env['MOCK_GROUP'] = os.path.join(self.test_dir, 'etc', 'group')
        env['LC_ALL'] = SIMULATED_SYSTEM_LOCALE

        self.log = tempfile.NamedTemporaryFile()
        if os.getenv('VALGRIND') != None:
            daemon_path = ['valgrind', self.daemon_path, '--debug']
        else:
            daemon_path = [self.daemon_path, '--debug']

        self.daemon = subprocess.Popen(daemon_path,
                                       env=env, stdout=self.log,
                                       stderr=subprocess.STDOUT)

        # wait until the daemon gets online
        timeout = 100

        if os.getenv('VALGRIND') != None:
            timeout = timeout * 33

        while timeout > 0:
            time.sleep(0.1)
            timeout -= 1
            try:
                self.get_dbus_property('DaemonVersion')
                break
            except GLib.GError:
                pass
        else:
            self.fail('daemon did not start in 10 seconds')

        self.proxy = Gio.DBusProxy.new_sync(
            self.dbus, Gio.DBusProxyFlags.DO_NOT_AUTO_START, None, AD,
            AD_PATH, AD, None)

        self.assertEqual(self.daemon.poll(), None, 'daemon crashed')

    def stop_daemon(self):
        '''Stop the daemon if it is running.'''

        if self.daemon:
            try:
                self.daemon.kill()
            except OSError:
                pass
            self.daemon.wait()
        self.daemon = None
        self.proxy = None

    def polkitd_start(self):
        if self._polkitd:
            return

        (self._polkitd, self._polkitd_obj) = self.spawn_server_template(
            'polkitd', {})
        self.addCleanup(self.stop_server, '_polkitd', '_polkitd_obj')

        return self._polkitd

    def stop_server(self, proc_attr, obj_attr):
        proc = getattr(self, proc_attr, None)
        if proc is None:
            return

        proc.terminate()
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired as e:
            proc.kill()

        delattr(self, proc_attr)
        delattr(self, obj_attr)

    def get_dbus_property(self, name):
        '''Get property value from daemon D-Bus interface.'''

        proxy = Gio.DBusProxy.new_sync(
            self.dbus, Gio.DBusProxyFlags.DO_NOT_AUTO_START, None, AD,
            AD_PATH, 'org.freedesktop.DBus.Properties', None)
        return proxy.Get('(ss)', AD, name)

    def get_user_dbus_property(self, user, name):
        '''Get property value from user D-Bus interface.'''

        proxy = Gio.DBusProxy.new_sync(
            self.dbus, Gio.DBusProxyFlags.DO_NOT_AUTO_START, None, AD,
            user, 'org.freedesktop.DBus.Properties', None)
        return proxy.Get('(ss)', 'org.freedesktop.Accounts.User', name)

    def have_text_in_log(self, text):
        return self.count_text_in_log(text) > 0

    def count_text_in_log(self, text):
        with open(self.log.name) as f:
            return f.read().count(text)

    def assertEventually(self, condition, message=None, timeout=50):
        '''Assert that condition function eventually returns True.

        Timeout is in deciseconds, defaulting to 50 (5 seconds). message is
        printed on failure.
        '''
        while timeout >= 0:
            context = GLib.MainContext.default()
            while context.iteration(False):
                pass
            if condition():
                break
            timeout -= 1
            time.sleep(0.1)
        else:
            self.fail(message or 'timed out waiting for ' + str(condition))

    #
    # Actual test cases
    #

    def test_languages(self):
        '''test that languages are correctly migrated'''

        self.polkitd_start()
        self._polkitd_obj.SetAllowed(['org.freedesktop.accounts.change-own-user-data',
            'org.freedesktop.accounts.user-administration'])

        self.start_daemon()

        res = self.proxy.call_sync('ListCachedUsers', GLib.Variant('()', ()), 0, -1, None)
        user = res[0][0]

        user_proxy = Gio.DBusProxy.new_sync(
                self.dbus, Gio.DBusProxyFlags.DO_NOT_AUTO_START, None, AD,
                user, AD_USER, None)
        user_proxy.call_sync('SetLanguage', GLib.Variant('(s)', ('en_GB.UTF-8',)), 0, -1, None)
        self.assertEqual(self.get_user_dbus_property(user, 'Language'), 'en_GB.UTF-8')
        self.assertEqual(self.get_user_dbus_property(user, 'Languages'), ['en_GB.UTF-8'])
        self.assertEqual(self.proxy.GetUsersLanguages(), ['en_GB.UTF-8', SIMULATED_SYSTEM_LOCALE])

        user_proxy.call_sync('SetLanguages', GLib.Variant('(as)', (['fr_FR.UTF-8', 'en_GB.UTF-8'],)), 0, -1, None)
        self.assertEqual(self.get_user_dbus_property(user, 'Language'), 'fr_FR.UTF-8')
        self.assertEqual(self.get_user_dbus_property(user, 'Languages'), ['fr_FR.UTF-8', 'en_GB.UTF-8'])
        self.assertEqual(self.proxy.GetUsersLanguages(), ['en_GB.UTF-8', SIMULATED_SYSTEM_LOCALE, 'fr_FR.UTF-8'])

        user_proxy.call_sync('SetLanguage', GLib.Variant('(s)', ('en_US.UTF-8',)), 0, -1, None)
        self.assertEqual(self.get_user_dbus_property(user, 'Language'), 'en_US.UTF-8')
        self.assertEqual(self.get_user_dbus_property(user, 'Languages'), ['en_US.UTF-8'])
        self.assertEqual(self.proxy.GetUsersLanguages(), [SIMULATED_SYSTEM_LOCALE, 'en_US.UTF-8'])

        user_proxy.call_sync('SetLanguages', GLib.Variant('(as)', (['fr_FR.UTF-8', 'en_GB.UTF-8'],)), 0, -1, None)
        self.assertEqual(self.get_user_dbus_property(user, 'Languages'), ['fr_FR.UTF-8', 'en_GB.UTF-8'])
        self.assertEqual(self.proxy.GetUsersLanguages(), ['en_GB.UTF-8', SIMULATED_SYSTEM_LOCALE, 'fr_FR.UTF-8'])

        user_proxy.call_sync('SetLanguages', GLib.Variant('(as)', ([''],)), 0, -1, None)
        self.assertEqual(self.get_user_dbus_property(user, 'Language'), '')
        self.assertEqual(self.get_user_dbus_property(user, 'Languages'), [''])
        self.assertEqual(self.proxy.GetUsersLanguages(), [SIMULATED_SYSTEM_LOCALE])

    def test_language(self):
        '''check that language setting are verified'''

        self.polkitd_start()
        self._polkitd_obj.SetAllowed(['org.freedesktop.accounts.change-own-user-data',
            'org.freedesktop.accounts.user-administration'])

        self.start_daemon()

        res = self.proxy.call_sync('ListCachedUsers', GLib.Variant('()', ()), 0, -1, None)
        user = res[0][1]

        user_proxy = Gio.DBusProxy.new_sync(
                self.dbus, Gio.DBusProxyFlags.DO_NOT_AUTO_START, None, AD,
                user, AD_USER, None)
        user_proxy.call_sync('SetLanguage', GLib.Variant('(s)', ('en_GB.UTF-8',)), 0, -1, None)
        self.assertEqual(self.get_user_dbus_property(user, 'Language'), 'en_GB.UTF-8')

        with self.assertRaises(gi.repository.GLib.GError) as cm:
            user_proxy.call_sync('SetLanguage', GLib.Variant('(s)', ('blahblahblah',)), 0, -1, None)

        self.assertIn('is not a valid XPG-formatted locale', str(cm.exception))


    def test_user_properties(self):
        '''check a user's properties'''

        self.start_daemon()

        res = self.proxy.call_sync('ListCachedUsers', GLib.Variant('()', ()), 0, -1, None)
        self.assertIsNotNone(res)
        user = res[0][1]
        self.assertEqual(user, AD_USER_PATH + '1001')

        self.assertEqual(self.get_user_dbus_property(user, 'IconFile'), self.test_dir + '/var/lib/AccountsService/icons/rupert')

        # gdbus call --system --dest org.freedesktop.Accounts --object-path /org/freedesktop/Accounts --method org.freedesktop.Accounts.ListCachedUsers

        self.stop_daemon()

    def test_startup(self):
        '''startup test'''

        self.start_daemon()

        process = subprocess.Popen(['gdbus', 'introspect', '--system', '--dest', AD, '--object-path', AD_PATH])
        process.wait()
        # print (self.get_dbus_property('DaemonVersion'))

        self.stop_daemon()


if __name__ == '__main__':
    unittest.main()
