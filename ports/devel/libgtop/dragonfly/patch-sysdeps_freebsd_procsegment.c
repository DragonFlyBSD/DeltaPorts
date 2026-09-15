--- sysdeps/freebsd/procsegment.c.orig
+++ sysdeps/freebsd/procsegment.c
@@ -67,8 +67,13 @@
 		return;
 	}
 
+#ifdef __DragonFly__
+	buf->text_rss = pinfo[0].kp_vm_tsize * pagesize;
+	buf->data_rss = pinfo[0].kp_vm_dsize * pagesize;
+#else
 	buf->text_rss = pinfo[0].ki_tsize * pagesize;
 	buf->data_rss = pinfo[0].ki_dsize * pagesize;
+#endif
 
 	buf->flags = _glibtop_sysdeps_proc_segment;
 }
