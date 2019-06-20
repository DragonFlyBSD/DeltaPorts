--- tools/gyp/pylib/gyp/common.py.orig	2019-03-05 15:16:33 UTC
+++ tools/gyp/pylib/gyp/common.py
@@ -421,6 +421,8 @@ def GetFlavor(params):
     return flavors[sys.platform]
   if sys.platform.startswith('sunos'):
     return 'solaris'
+  if sys.platform.startswith('dragon'):
+    return 'freebsd'
   if sys.platform.startswith('freebsd'):
     return 'freebsd'
   if sys.platform.startswith('openbsd'):
