diff --git chrome/browser/flag_descriptions.cc chrome/browser/flag_descriptions.cc
index ca51dc0d6312..7d06988a3e26 100644
--- chrome/browser/flag_descriptions.cc
+++ chrome/browser/flag_descriptions.cc
@@ -7971,7 +7971,7 @@ const char kWaylandUiScalingDescription[] =
 const char kAudioBackendName[] =
     "Audio Backend";
 const char kAudioBackendDescription[] =
-#if BUILDFLAG(IS_OPENBSD)
+#if BUILDFLAG(IS_OPENBSD) || BUILDFLAG(IS_DRAGONFLY)
     "Select the desired audio backend to use. The default is sndio.";
 #elif BUILDFLAG(IS_FREEBSD)
     "Select the desired audio backend to use. The default will automatically "
