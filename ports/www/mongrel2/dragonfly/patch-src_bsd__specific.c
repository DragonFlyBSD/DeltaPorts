--- src/bsd_specific.c.intermediate	2016-08-02 13:17:34 UTC
+++ src/bsd_specific.c
@@ -40,7 +40,7 @@
 #include <dbg.h>
 
 
-#if defined(__APPLE__) || defined(__FreeBSD__)
+#if defined(__APPLE__) || defined(__FreeBSD__) || defined(__DragonFly__)
 
 /**
  * BSD version of sendfile, which is OSX and FreeBSD mostly.
@@ -55,7 +55,7 @@ int bsd_sendfile(int out_fd, int in_fd,
         fdwait(out_fd, 'w');
 #if defined(__APPLE__)
         rc = sendfile(in_fd, out_fd, *offset, &my_count, NULL, 0);
-#elif defined(__FreeBSD__)
+#elif defined(__FreeBSD__) || defined(__DragonFly__)
         rc = sendfile(in_fd, out_fd, *offset, count, NULL, &my_count, 0);
 #endif
         *offset += my_count;
