--- build/standalone.gypi.orig	2012-12-09 04:50:14.000000000 +0100
+++ build/standalone.gypi	2013-01-21 09:46:58.732744000 +0100
@@ -39,6 +39,7 @@
         'variables': {
           'conditions': [
             ['OS=="linux" or OS=="freebsd" or OS=="openbsd" or \
+               OS=="dragonfly" or \
                OS=="netbsd" or OS=="mac"', {
               # This handles the Unix platforms we generally deal with.
               # Anything else gets passed through, which probably won't work
@@ -90,6 +91,7 @@
   },
   'conditions': [
     ['OS=="linux" or OS=="freebsd" or OS=="openbsd" or OS=="solaris" \
+       or OS=="dragonfly" \
        or OS=="netbsd"', {
       'target_defaults': {
         'cflags': [ '-Wall', '<(werror)', '-W', '-Wno-unused-parameter',
