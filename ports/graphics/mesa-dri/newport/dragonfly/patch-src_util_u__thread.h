--- src/util/u_thread.h.orig	2020-02-13 19:08:32 UTC
+++ src/util/u_thread.h
@@ -71,7 +71,7 @@ static inline void u_thread_setname( con
 #if defined(HAVE_PTHREAD)
 #if DETECT_OS_LINUX || DETECT_OS_CYGWIN || DETECT_OS_SOLARIS
    pthread_setname_np(pthread_self(), name);
-#elif DETECT_OS_FREEBSD || DETECT_OS_OPENBSD
+#elif DETECT_OS_FREEBSD || DETECT_OS_OPENBSD || DETECT_OS_DRAGONFLY
    pthread_set_name_np(pthread_self(), name);
 #elif DETECT_OS_NETBSD
    pthread_setname_np(pthread_self(), "%s", (void *)name);
