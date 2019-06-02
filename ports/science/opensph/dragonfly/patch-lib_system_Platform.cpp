--- lib/system/Platform.cpp.intermediate	2019-06-02 19:45:38.000000000 +0000
+++ lib/system/Platform.cpp
@@ -103,18 +103,18 @@ private:
 
 public:
     CpuUsage() {
-#if !defined(__FreeBSD__)
+#if !defined(__FreeBSD__) && !defined(__DragonFly__)
         FILE* file;
 #endif
         struct tms timeSample;
-#if !defined(__FreeBSD__)
+#if !defined(__FreeBSD__) && !defined(__DragonFly__)
         char line[128];
 #endif
 
         lastCpu = times(&timeSample);
         lastSysCpu = timeSample.tms_stime;
         lastUserCpu = timeSample.tms_utime;
-#if !defined(__FreeBSD__)
+#if !defined(__FreeBSD__) && !defined(__DragonFly__)
         file = fopen("/proc/cpuinfo", "r");
         numProcessors = 0;
         while (fgets(line, 128, file) != NULL) {
