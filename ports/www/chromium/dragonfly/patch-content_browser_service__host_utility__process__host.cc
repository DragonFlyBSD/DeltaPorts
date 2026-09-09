diff --git content/browser/service_host/utility_process_host.cc content/browser/service_host/utility_process_host.cc
index 3da4ccddada0..b814809aa8ce 100644
--- content/browser/service_host/utility_process_host.cc
+++ content/browser/service_host/utility_process_host.cc
@@ -363,7 +363,7 @@ bool UtilityProcessHost::StartProcess() {
         switches::kMuteAudio,
         switches::kUseFileForFakeAudioCapture,
 #if BUILDFLAG(IS_LINUX) || BUILDFLAG(IS_CHROMEOS) || BUILDFLAG(IS_FREEBSD) || \
-    BUILDFLAG(IS_SOLARIS)
+    BUILDFLAG(IS_SOLARIS) || BUILDFLAG(IS_DRAGONFLY)
         switches::kAlsaInputDevice,
         switches::kAlsaOutputDevice,
 #endif
