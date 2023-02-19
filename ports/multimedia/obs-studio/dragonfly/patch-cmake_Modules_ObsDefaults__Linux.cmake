--- cmake/Modules/ObsDefaults_Linux.cmake.orig	2022-11-05 18:07:13 UTC
+++ cmake/Modules/ObsDefaults_Linux.cmake
@@ -122,7 +122,7 @@ macro(setup_obs_project)
     set(CPACK_GENERATOR "DEB")
     set(CPACK_DEBIAN_PACKAGE_SHLIBDEPS ON)
     set(CPACK_SET_DESTDIR ON)
-  elseif(OS_FREEBSD)
+  elseif(OS_FREEBSD OR OS_DRAGONFLY)
     option(ENABLE_CPACK_GENERATOR
            "Enable FreeBSD CPack generator (experimental)" OFF)
 
