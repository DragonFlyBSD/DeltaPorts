--- VTK/IO/LSDyna/private/LSDynaFamily.cxx.orig	2014-01-11 14:02:08.000000000 +0000
+++ VTK/IO/LSDyna/private/LSDynaFamily.cxx
@@ -33,7 +33,10 @@
 namespace
 {
 //Documentation on why the exemption
-#define USE_STAT_64 VTK_SIZEOF_ID_TYPE==8 && !defined _DARWIN_FEATURE_64_BIT_INODE && !defined __FreeBSD__
+#define USE_STAT_64 VTK_SIZEOF_ID_TYPE==8 \
+ && !defined _DARWIN_FEATURE_64_BIT_INODE \
+ && !defined __FreeBSD__ \
+ && !defined __DragonFly__
 //OS X and FreeBSD use stat instead of stat64
 #if (USE_STAT_64)
 //64bit
