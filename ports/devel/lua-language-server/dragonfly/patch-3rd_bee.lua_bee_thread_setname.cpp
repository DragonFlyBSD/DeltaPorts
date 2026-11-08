--- 3rd/bee.lua/bee/thread/setname.cpp.orig	2026-11-08 00:00:00.000000000 +0000
+++ 3rd/bee.lua/bee/thread/setname.cpp	2026-11-08 00:00:00.000000000 +0000
@@ -11,7 +11,7 @@
 #            include <sys/prctl.h>
 #        endif
 #    endif
-#    if defined(__FreeBSD__) || defined(__OpenBSD__)
+#    if defined(__FreeBSD__) || defined(__DragonFly__) || defined(__OpenBSD__)
 #        include <pthread_np.h>
 #    endif
 #endif
@@ -67,7 +67,7 @@
 #    endif
 #elif defined(__NetBSD__)
         pthread_setname_np(pthread_self(), "%s", (void*)name.data());
-#elif defined(__FreeBSD__) || defined(__OpenBSD__)
+#elif defined(__FreeBSD__) || defined(__DragonFly__) || defined(__OpenBSD__)
         pthread_set_name_np(pthread_self(), name.data());
 #endif
     }
