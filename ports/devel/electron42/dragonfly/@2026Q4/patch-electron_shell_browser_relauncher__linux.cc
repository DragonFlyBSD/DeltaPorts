--- electron/shell/browser/relauncher_linux.cc.orig
+++ electron/shell/browser/relauncher_linux.cc
@@ -6,7 +6,7 @@
 
 #include <fcntl.h>
 #include <signal.h>
-#if !defined(__FreeBSD__)
+#if !defined(__FreeBSD__) && !defined(__DragonFly__)
 #include <sys/prctl.h>
 #include <sys/signalfd.h>
 #endif
@@ -30,7 +30,7 @@
   base::ScopedFD relauncher_sync_fd(kRelauncherSyncFD);
   static const auto signum = SIGUSR2;
 
-#if defined(__FreeBSD__)
+#if defined(__FreeBSD__) || defined(__DragonFly__)
   PLOG(ERROR) << "signalfd & prctl";
   fprintf(stderr, "Not Implemented signalfd & prctl in atom/browser/relauncher_linux.cc");
 #else
