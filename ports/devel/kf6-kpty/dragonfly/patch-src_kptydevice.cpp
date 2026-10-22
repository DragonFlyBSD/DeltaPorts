--- src/kptydevice.cpp.orig	2025-05-08 09:00:00 UTC
+++ src/kptydevice.cpp
@@ -32,7 +32,7 @@
 #if HAVE_SYS_TIME_H
 #include <sys/time.h>
 #endif
 
-#if defined(Q_OS_FREEBSD) || defined(Q_OS_MAC)
+#if defined(Q_OS_FREEBSD) || defined(Q_OS_DRAGONFLY) || defined(Q_OS_MAC)
 // "the other end's output queue size" -- that is is our end's input
 #define PTY_BYTES_AVAILABLE TIOCOUTQ
 #elif defined(TIOCINQ)
