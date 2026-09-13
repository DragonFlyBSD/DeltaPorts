--- sysdeps/freebsd/procmap.c.orig
+++ sysdeps/freebsd/procmap.c
@@ -24,6 +24,7 @@
 #include <glibtop/error.h>
 #include <glibtop/procmap.h>
 
+#ifndef __DragonFly__
 #include <glibtop_suid.h>
 
 #include <kvm.h>
@@ -222,6 +223,9 @@
 	    } /* end-if IS_UFS */
 }
 #endif
+#else
+static const unsigned long _glibtop_sysdeps_proc_map = 0;
+#endif
 
 /* Init function. */
 
@@ -237,6 +241,10 @@
 glibtop_get_proc_map_p (glibtop *server, glibtop_proc_map *buf,
                         pid_t pid)
 {
+#ifdef __DragonFly__
+	memset (buf, 0, sizeof (glibtop_proc_map));
+	return NULL;
+#else
         struct kinfo_proc *pinfo;
         struct vm_map_entry entry, *first;
         struct vmspace vmspace;
@@ -404,4 +412,5 @@
         buf->total  = (guint64) (buf->number * buf->size);
 
         return (glibtop_map_entry*) g_array_free(maps, FALSE);
+#endif
 }
