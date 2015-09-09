--- sysdeps/freebsd/proctime.c.orig	2015-08-17 18:59:37 UTC
+++ sysdeps/freebsd/proctime.c
@@ -92,7 +92,9 @@ glibtop_get_proc_time_p (glibtop *server
 	}
 
 	buf->frequency = (ci.stathz ? ci.stathz : ci.hz);
+#ifndef __DragonFly__ /* TODO */
 	buf->rtime = pinfo [0].ki_runtime * buf->frequency / 1000000;
+#endif
 	buf->flags = _glibtop_sysdeps_proc_time;
 
 	/*
@@ -101,11 +103,13 @@ glibtop_get_proc_time_p (glibtop *server
 	  I have no idea what this PS_INMEM is, but it works perfectly
 	  without this check.
 	 */
+#ifndef __DragonFly__ /* TODO */
 	buf->utime = tv2sec_freq (pinfo [0].ki_rusage.ru_utime, buf->frequency);
 	buf->stime = tv2sec_freq (pinfo [0].ki_rusage.ru_stime, buf->frequency);
 	buf->cutime = tv2sec_freq (pinfo [0].ki_childtime, buf->frequency);
 #if (__FreeBSD_version >= 600006) || defined(__FreeBSD_kernel__)
 	buf->cstime = tv2sec_freq (pinfo [0].ki_rusage_ch.ru_stime, buf->frequency);
+#endif
 #else
 	   buf->cstime = 0;
 #endif
