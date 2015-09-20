--- /tmp/watchtab.h	2013-09-14 18:09:32.000000000 +0300
+++ watchtab.h	2015-09-20 18:35:20.000000000 +0300
@@ -23,6 +23,9 @@
 #include <sys/queue.h>
 #include <sys/types.h>
 #include <unistd.h>
+#ifdef __DragonFly__
+#include <sys/timespec.h>
+#endif
 
 
 /********************
