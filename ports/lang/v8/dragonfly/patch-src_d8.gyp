--- src/d8.gyp.orig	2013-01-21 09:52:18.493191000 +0100
+++ src/d8.gyp	2013-01-21 09:52:48.873233000 +0100
@@ -62,6 +62,7 @@
               'sources': [ 'd8-readline.cc' ],
             }],
             ['(OS=="linux" or OS=="mac" or OS=="freebsd" or OS=="netbsd" \
+               or OS=="dragonfly" \
                or OS=="openbsd" or OS=="solaris" or OS=="android")', {
               'sources': [ 'd8-posix.cc', ]
             }],
