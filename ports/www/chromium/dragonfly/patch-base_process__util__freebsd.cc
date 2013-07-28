--- base/process_util_freebsd.cc.intermediate	2013-07-27 08:42:10.000000000 +0000
+++ base/process_util_freebsd.cc
@@ -25,6 +25,16 @@
 #include "base/strings/string_split.h"
 #include "base/sys_info.h"
 
+#ifdef __DragonFly__
+#define ki_pid    kp_pid
+#define ki_pgid   kp_pgid
+#define ki_ppid   kp_ppid
+#define ki_stat   kp_stat
+#define ki_size   kp_vm_map_size
+#define ki_rssize kp_vm_rssize
+#define ki_pctcpu kp_lwp.kl_pctcpu
+#endif
+
 namespace base {
 
 ProcessId GetParentProcessId(ProcessHandle process) {
@@ -39,6 +49,9 @@ ProcessId GetParentProcessId(ProcessHand
 }
 
 FilePath GetProcessExecutablePath(ProcessHandle process) {
+#ifdef __DragonFly__
+  return FilePath("/usr/local/bin/chrome");
+#else
   char pathname[PATH_MAX];
   size_t length;
   int mib[] = { CTL_KERN, KERN_PROC, KERN_PROC_PATHNAME, process };
@@ -51,6 +64,7 @@ FilePath GetProcessExecutablePath(Proces
   }
 
   return FilePath(std::string(pathname));
+#endif
 }
 
 ProcessIterator::ProcessIterator(const ProcessFilter* filter)
