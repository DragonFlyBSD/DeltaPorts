diff --git third_party/perfetto/include/perfetto/base/time.h third_party/perfetto/include/perfetto/base/time.h
index 104ae37f9822..f3aa35fd57f5 100644
--- third_party/perfetto/include/perfetto/base/time.h
+++ third_party/perfetto/include/perfetto/base/time.h
@@ -227,7 +227,7 @@ inline TimeNanos GetTimeInternalNs(clockid_t clk_id) {
 // Return ns from boot. Conversely to GetWallTimeNs, this clock counts also time
 // during suspend (when supported).
 inline TimeNanos GetBootTimeNs() {
-#if defined(__FreeBSD__)
+#if defined(__FreeBSD__) || defined(__DragonFly__)
   return GetTimeInternalNs(kWallTimeClockSource);
 #else
   // Determine if CLOCK_BOOTTIME is available on the first call.
@@ -247,7 +247,7 @@ inline TimeNanos GetWallTimeNs() {
 inline TimeNanos GetWallTimeRawNs() {
 #if defined(__OpenBSD__)
   return GetTimeInternalNs(CLOCK_MONOTONIC);
-#elif defined(__FreeBSD__)
+#elif defined(__FreeBSD__) || defined(__DragonFly__)
   return GetTimeInternalNs(CLOCK_MONOTONIC_FAST);
 #else
   return GetTimeInternalNs(CLOCK_MONOTONIC_RAW);
