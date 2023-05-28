--- cmake/Modules/ObsDefaults_Linux.cmake.orig	2023-02-04 10:17:10 UTC
+++ cmake/Modules/ObsDefaults_Linux.cmake
@@ -121,7 +121,7 @@ macro(setup_obs_project)
     set(CPACK_DEBIAN_PACKAGE_SHLIBDEPS ON)
     set(CPACK_SET_DESTDIR ON)
     set(CPACK_DEBIAN_DEBUGINFO_PACKAGE ON)
-  elseif(OS_FREEBSD)
+  elseif(OS_FREEBSD OR OS_DRAGONFLY)
     option(ENABLE_CPACK_GENERATOR
            "Enable FreeBSD CPack generator (experimental)" OFF)
 
