--- node_modules/node-pty/src/unix/pty.cc.orig
+++ node_modules/node-pty/src/unix/pty.cc
@@ -41,7 +41,7 @@
 #include <sys/syscall.h>
 #elif defined(__APPLE__)
 #include <util.h>
-#elif defined(__FreeBSD__)
+#elif defined(__FreeBSD__) || defined(__DragonFly__)
 #include <libutil.h>
 #include <termios.h>
 #elif defined(__OpenBSD__)
@@ -112,7 +112,7 @@
   int exit_code = 0, signal_code = 0;
 };
 
-#if defined(__linux__) || defined(__FreeBSD__)
+#if defined(__linux__) || defined(__FreeBSD__) || defined(__DragonFly__)
 
 static int
 SetCloseOnExec(int fd) {
