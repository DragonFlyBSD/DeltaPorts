--- sysdeps/common/fsusage.c.orig
+++ sysdeps/common/fsusage.c
@@ -140,7 +140,7 @@
 }
 
 
-#elif defined(__FreeBSD__)
+#elif defined(__FreeBSD__) || defined(__DragonFly__)
 void
 _glibtop_freebsd_get_fsusage_read_write(glibtop *server,
 					glibtop_fsusage *buf,
@@ -279,7 +279,7 @@
     return;
 
 #if (defined(sun) || defined(__sun)) && (defined(__SVR4) || defined(__svr4__)) \
-	|| defined(__FreeBSD__) || defined(__OpenBSD__)
+	|| defined(__FreeBSD__) || defined(__OpenBSD__) || defined(__DragonFly__)
   /* Solaris but not SunOS and FreeBSD */
   buf->block_size = fsd.f_frsize;
 #else
