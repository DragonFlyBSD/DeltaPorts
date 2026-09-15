--- sysdeps/freebsd/procio.c.orig
+++ sysdeps/freebsd/procio.c
@@ -74,7 +74,9 @@
 
 	   FIXME: seems the blocksize is 1024 but ...
 	*/
+#ifndef __DragonFly__
 	buf->disk_rbytes = pinfo->ki_rusage.ru_inblock << 10;
 	buf->disk_wbytes = pinfo->ki_rusage.ru_oublock << 10;
 	buf->flags = _glibtop_sysdeps_proc_io;
+#endif
 }
