--- third_party/libusb/libusb.gyp.orig	2014-01-05 14:47:38.346312000 +0000
+++ third_party/libusb/libusb.gyp
@@ -93,6 +93,12 @@
           ],
           'msvs_disabled_warnings': [ 4267 ],
         }],
+        ['OS == "dragonfly"', {
+          'type': 'none',
+          'sources/': [
+            ['exclude', '^src/libusb/'],
+          ],
+        }],
         ['OS == "freebsd"', {
           'type': 'none',
           'sources/': [
