--- build/common.gypi.orig	2012-12-09 04:50:14.000000000 +0100
+++ build/common.gypi	2013-01-21 09:43:54.562487000 +0100
@@ -281,6 +281,7 @@
         },
       }],
       ['OS=="linux" or OS=="freebsd" or OS=="openbsd" or OS=="solaris" \
+         or OS=="dragonfly" \
          or OS=="netbsd"', {
         'conditions': [
           [ 'v8_no_strict_aliasing==1', {
@@ -292,6 +293,7 @@
         'defines': [ '__C99FEATURES__=1' ],  # isinf() etc.
       }],
       ['(OS=="linux" or OS=="freebsd" or OS=="openbsd" or OS=="solaris" \
+         or OS=="dragonfly" \
          or OS=="netbsd" or OS=="mac" or OS=="android") and \
         (v8_target_arch=="arm" or v8_target_arch=="ia32" or \
          v8_target_arch=="mipsel")', {
@@ -325,7 +327,7 @@
           }],
         ],
       }],
-      ['OS=="freebsd" or OS=="openbsd"', {
+      ['OS=="freebsd" or OS=="openbsd" or OS=="dragonfly"', {
         'cflags': [ '-I/usr/local/include' ],
       }],
       ['OS=="netbsd"', {
@@ -364,7 +366,9 @@
           ['v8_enable_extra_checks==1', {
             'defines': ['ENABLE_EXTRA_CHECKS',],
           }],
-          ['OS=="linux" or OS=="freebsd" or OS=="openbsd" or OS=="netbsd"', {
+          ['OS=="linux" or OS=="freebsd" or OS=="openbsd" \
+            or OS=="dragonfly" \
+            or OS=="netbsd"', {
             'cflags': [ '-Wall', '<(werror)', '-W', '-Wno-unused-parameter',
                         '-Wnon-virtual-dtor', '-Woverloaded-virtual' ],
           }],
@@ -398,6 +402,7 @@
             'defines': ['ENABLE_EXTRA_CHECKS',],
           }],
           ['OS=="linux" or OS=="freebsd" or OS=="openbsd" or OS=="netbsd" \
+            or OS=="dragonfly" \
             or OS=="android"', {
             'cflags!': [
               '-O2',
