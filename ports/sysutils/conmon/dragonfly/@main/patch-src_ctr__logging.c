--- src/ctr_logging.c.orig
+++ src/ctr_logging.c
@@ -7,6 +7,11 @@
 #include <sys/stat.h>
 #include <limits.h>
 
+#if defined(__DragonFly__) && !defined(O_PATH)
+/* DragonFly has no O_PATH; 0x1000000 matches the FreeBSD value. */
+#define O_PATH 0x1000000
+#endif
+
 // if the systemd development files were found, we can log to systemd
 #ifdef USE_JOURNALD
 #include <systemd/sd-journal.h>
