--- lib/common/watchdog.c.orig	2018-05-15 15:14:57 UTC
+++ lib/common/watchdog.c
@@ -95,6 +95,13 @@ sysrq_trigger(char t)
     return;
 }
 
+#ifdef __DragonFly__
+/* XXX */
+static int sigqueue(pid_t pid, int signal, union sigval sigvalue)
+{
+  return -1;
+}
+#endif
 
 static void
 pcmk_panic_local(void) 
