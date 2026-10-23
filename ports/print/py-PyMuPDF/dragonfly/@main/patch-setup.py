--- setup.py.orig	2026-03-31 15:39:24 UTC
+++ setup.py
@@ -556,7 +556,7 @@
 
 linux = sys.platform.startswith( 'linux') or 'gnu' in sys.platform
 openbsd = sys.platform.startswith( 'openbsd')
-freebsd = sys.platform.startswith( 'freebsd')
+freebsd = sys.platform.startswith( ('freebsd', 'dragonfly'))
 darwin = sys.platform.startswith( 'darwin')
 windows = platform.system() == 'Windows' or platform.system().startswith('CYGWIN')
 msys2 = platform.system().startswith('MSYS_NT-')
