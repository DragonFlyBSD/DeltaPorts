--- runtime/druntime/src/core/sys/posix/dirent.d.orig	2019-04-06 12:24:12.000000000 +0000
+++ runtime/druntime/src/core/sys/posix/dirent.d	2019-04-21 14:13:39.676998000 +0000
@@ -152,11 +152,13 @@
     align(4)
     struct dirent
     {
-        uint      d_fileno;
+        ino_t     d_fileno;
+        off_t     d_off;
         ushort    d_reclen;
         ubyte     d_type;
-        ubyte     d_namlen;
-        char[256] d_name = 0;
+        ushort    d_namlen;
+        ushort    d_pad1;
+        char[256] d_name;
     }
 
     alias void* DIR;
