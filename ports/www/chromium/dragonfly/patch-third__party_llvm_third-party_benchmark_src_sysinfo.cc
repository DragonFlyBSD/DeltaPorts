diff --git third_party/llvm/third-party/benchmark/src/sysinfo.cc third_party/llvm/third-party/benchmark/src/sysinfo.cc
index 3993ae17f7fc..dfbd155a8e26 100644
--- third_party/llvm/third-party/benchmark/src/sysinfo.cc
+++ third_party/llvm/third-party/benchmark/src/sysinfo.cc
@@ -693,6 +693,8 @@ double GetCPUCyclesPerSecond(CPUInfo::Scaling scaling) {
   constexpr auto* freqStr =
 #if defined(BENCHMARK_OS_FREEBSD) || defined(BENCHMARK_OS_NETBSD)
       "machdep.tsc_freq";
+#elif defined BENCHMARK_OS_DRAGONFLY
+      "hw.tsc_frequency";
 #elif defined BENCHMARK_OS_OPENBSD
       "hw.cpuspeed";
 #elif defined BENCHMARK_OS_DRAGONFLY
