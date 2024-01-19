[![Build Status](https://gitlab.freedesktop.org/accountsservice/accountsservice/badges/master/build.svg)](https://gitlab.freedesktop.org/accountsservice/accountsservice/pipelines)

Overview
========

The AccountsService project provides

 o  A set of D-Bus interfaces for querying and manipulating
    user account information.

 o  An implementation of these interfaces based on the usermod(8),
    useradd(8) and userdel(8) commands.

License
=======

See the COPYING file.

Installation
============

The AccountsService uses the following libraries:

 - GLib, polkit, libxcrypt or the crypt library
 - systemd for libsystemd

At runtime, the daemon uses the polkit and logind D-Bus services
and utilities from the shadow-utils package.

Contributing
============
As with other projects hosted on freedesktop.org, accountsservice follows its
Code of Conduct, based on the Contributor Covenant. Please conduct
yourself in a respectful and civilized manner when using the above
mailing lists, bug trackers, etc:

       https://www.freedesktop.org/wiki/CodeOfConduct
