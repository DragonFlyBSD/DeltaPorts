--- sysdeps/freebsd/open.c.orig
+++ sysdeps/freebsd/open.c
@@ -43,6 +43,10 @@
 	server->real_ncpu = ncpus - 1;
 	server->ncpu = MIN(GLIBTOP_NCPU - 1, server->real_ncpu);
 
+#ifdef __DragonFly__
+	server->os_version_code = __DragonFly_version;
+#else
 	server->os_version_code = __FreeBSD_version;
+#endif
 
 }
