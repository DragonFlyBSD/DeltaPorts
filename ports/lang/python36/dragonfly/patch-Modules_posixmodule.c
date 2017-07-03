--- Modules/posixmodule.c.orig	2017-03-21 06:32:38.000000000 +0000
+++ Modules/posixmodule.c	2017-07-03 00:53:50.000000000 +0000
@@ -66,6 +66,7 @@
 #ifdef HAVE_SYS_WAIT_H
 #include <sys/wait.h>           /* For WNOHANG */
 #endif
+#include <sys/procctl.h>
 
 #ifdef HAVE_SIGNAL_H
 #include <signal.h>
