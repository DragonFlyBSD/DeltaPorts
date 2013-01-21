--- tools/gyp/v8.gyp.orig	2012-12-09 04:50:16.000000000 +0100
+++ tools/gyp/v8.gyp	2013-01-21 11:06:57.329447000 +0100
@@ -683,6 +683,17 @@
                 ],
               },
             ],
+            ['OS=="dragonfly"', {
+                'link_settings': {
+                  'libraries': [
+                    '-L/usr/local/lib -lexecinfo',
+                ]},
+                'sources': [
+                  '../../src/platform-freebsd.cc',
+                  '../../src/platform-posix.cc'
+                ],
+              }
+            ],
             ['OS=="freebsd"', {
                 'link_settings': {
                   'libraries': [
