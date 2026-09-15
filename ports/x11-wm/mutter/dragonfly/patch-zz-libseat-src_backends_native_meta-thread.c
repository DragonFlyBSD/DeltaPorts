--- src/backends/native/meta-thread.c.orig
+++ src/backends/native/meta-thread.c
@@ -303,6 +303,12 @@
 request_realtime_scheduling (MetaThread  *thread,
                              GError     **error)
 {
+#ifndef RLIMIT_RTTIME
+  /* Real time scheduling via RealtimeKit is Linux-only. */
+  g_set_error_literal (error, G_IO_ERROR, G_IO_ERROR_NOT_SUPPORTED,
+                       "Real time scheduling requires RLIMIT_RTTIME (Linux)");
+  return FALSE;
+#else
   MetaThreadPrivate *priv = meta_thread_get_instance_private (thread);
   g_autoptr (GError) local_error = NULL;
   int64_t rttime;
@@ -369,6 +375,7 @@
     }
 
   return TRUE;
+#endif
 }
 
 static gboolean
@@ -536,7 +543,7 @@
   meta_profiler_register_thread (profiler, thread_context, priv->name);
 #endif
 
-  priv->kernel.thread_id = gettid ();
+  priv->kernel.thread_id = lwp_gettid ();
   priv->kernel.realtime_inhibit_count = 0;
 
   meta_thread_impl_setup (impl);
