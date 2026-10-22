--- src/hwcaps.c.intermediate	2026-09-04 10:16:34 UTC
+++ src/hwcaps.c
@@ -55,7 +55,14 @@ const char* CpuFeatures_GetBasePlatformPointer(void);
 // Implementation of GetElfHwcapFromGetauxval
 ////////////////////////////////////////////////////////////////////////////////
 
-#if defined(CPU_FEATURES_OS_FREEBSD)
+#if defined(__DragonFly__)
+#define AT_HWCAP 16
+#define AT_HWCAP2 26
+#define AT_PLATFORM 15
+#define AT_BASE_PLATFORM 24
+#endif
+
+#if defined(CPU_FEATURES_OS_FREEBSD) && !defined(__DragonFly__)
 #include <sys/auxv.h>
 static unsigned long GetElfHwcapFromGetauxval(uint32_t hwcap_type) {
   unsigned long val = 0;
