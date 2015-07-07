--- src/libutil/sysutil.cpp.orig	2014-11-25 07:10:44.000000000 +0200
+++ src/libutil/sysutil.cpp
@@ -40,6 +40,14 @@
 # include <sys/ioctl.h>
 #endif
 
+#ifdef __DragonFly__
+# include <sys/types.h>
+# include <sys/time.h>
+# include <sys/resource.h>
+# include <sys/ioctl.h>
+# include <unistd.h>
+#endif
+
 #if defined (__FreeBSD__) || defined (__FreeBSD_kernel__)
 # include <sys/types.h>
 # include <sys/resource.h>
@@ -128,6 +136,13 @@ Sysutil::memory_used (bool resident)
     // FIXME -- does somebody know a good method for figuring this out for
     // FreeBSD?
     return 0;   // Punt
+#elif defined(__DragonFly__)
+    // XXX better than nothing
+    size_t size = 0;
+    struct rusage rus;
+    getrusage(0, &rus);
+    size = size_t(rus.ru_maxrss * 1024);
+    return size;
 #else
     // No idea what platform this is
     ASSERT (0 && "Need to implement Sysutil::memory_used on this platform");
@@ -225,6 +240,11 @@ Sysutil::this_program_path ()
     int r = readlink ("/proc/self/exe", filename, size);
     ASSERT(r < int(size)); // user won't get the right answer if the filename is too long to store
     if (r > 0) filename[r] = 0; // readlink does not fill in the 0 byte
+#elif defined(__DragonFly__)
+    unsigned int size = sizeof(filename);
+    int r = readlink ("/proc/curproc/file", filename, size);
+    ASSERT(r < int(size)); // user won't get the right answer if the filename is too long to store
+    if (r > 0) filename[r] = 0;
 #elif defined(__APPLE__)
     // For info:  'man 3 dyld'
     unsigned int size = sizeof(filename);
@@ -275,7 +295,7 @@ Sysutil::terminal_columns ()
 {
     int columns = 80;   // a decent guess, if we have nothing more to go on
 
-#if defined(__linux__) || defined(__APPLE__) || defined(__FreeBSD__) || defined(__FreeBSD_kernel__) || defined(__GNU__)
+#if defined(__linux__) || defined(__APPLE__) || defined(__FreeBSD__) || defined(__FreeBSD_kernel__) || defined(__DragonFly__) || defined(__GNU__)
     struct winsize w;
     ioctl (0, TIOCGWINSZ, &w);
     columns = w.ws_col;
