--- lib/kptydevice.cpp.orig
+++ lib/kptydevice.cpp
@@ -47,7 +47,7 @@
 # include <sys/time.h>
 #endif
 
-#if defined(Q_OS_FREEBSD) || defined(Q_OS_MAC)
+#if defined(Q_OS_FREEBSD) || defined(Q_OS_DRAGONFLY) || defined(Q_OS_MAC)
   // "the other end's output queue size" - kinda braindead, huh?
 # define PTY_BYTES_AVAILABLE TIOCOUTQ
 #elif defined(TIOCINQ)
