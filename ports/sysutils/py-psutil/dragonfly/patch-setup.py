--- setup.py.orig	2014-03-02 12:01:13.366033000 +0000
+++ setup.py
@@ -114,7 +114,7 @@ elif sys.platform.startswith("darwin"):
         posix_extension,
     ]
 # FreeBSD
-elif sys.platform.startswith("freebsd"):
+elif sys.platform.startswith("freebsd") or sys.platform.startswith("dragon"):
     extensions = [Extension(
         '_psutil_bsd',
         sources=[
