--- src/hotspot/os/bsd/os_bsd.cpp.orig	2026-04-22 18:05:58 UTC
+++ src/hotspot/os/bsd/os_bsd.cpp
@@ -137,9 +137,18 @@
     #define _MACHINE_CPUFUNC_H_
   #endif
 #include <sys/user.h>
+#ifdef __DragonFly__
+#undef KERN_PROC_MIB
+#undef KINFO_PROC_T
+#undef KI_RSS
 #define KERN_PROC_MIB  KERN_PROC
 #define KINFO_PROC_T   kinfo_proc
+#define KI_RSS         kp_vm_rssize
+#else
+#define KERN_PROC_MIB  KERN_PROC
+#define KINFO_PROC_T   kinfo_proc
 #define KI_RSS         ki_rssize
+#endif
 #elif defined(__NetBSD__)
 #define KERN_PROC_MIB  KERN_PROC2
 #define KINFO_PROC_T   kinfo_proc2
