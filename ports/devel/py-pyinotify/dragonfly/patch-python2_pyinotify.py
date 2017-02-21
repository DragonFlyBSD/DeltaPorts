--- python2/pyinotify.py.orig	2015-06-04 18:34:41.000000000 +0300
+++ python2/pyinotify.py
@@ -204,7 +204,7 @@ class _CtypesLibcINotifyWrapper(INotifyW
         assert ctypes
 
         try_libc_name = 'c'
-        if sys.platform.startswith('freebsd'):
+        if sys.platform.startswith('freebsd') or sys.platform.startswith('dragonfly'):
             try_libc_name = 'inotify'
 
         libc_name = None
