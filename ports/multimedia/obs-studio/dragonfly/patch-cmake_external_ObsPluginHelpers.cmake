--- cmake/external/ObsPluginHelpers.cmake.orig	2022-11-05 18:07:13 UTC
+++ cmake/external/ObsPluginHelpers.cmake
@@ -8,7 +8,7 @@ include(GNUInstallDirs)
 if(${CMAKE_SYSTEM_NAME} STREQUAL "Darwin")
   set(OS_MACOS ON)
   set(OS_POSIX ON)
-elseif(${CMAKE_SYSTEM_NAME} MATCHES "Linux|FreeBSD|OpenBSD")
+elseif(${CMAKE_SYSTEM_NAME} MATCHES "Linux|FreeBSD|OpenBSD|DragonFly")
   set(OS_POSIX ON)
   string(TOUPPER "${CMAKE_SYSTEM_NAME}" _SYSTEM_NAME_U)
   set(OS_${_SYSTEM_NAME_U} ON)
