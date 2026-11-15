--- xf86drm.c.orig
+++ xf86drm.c
@@ -68,6 +68,10 @@
 #include <sys/pciio.h>
 #endif
 
+#if defined(__DragonFly__)
+#include <sys/param.h>		/* SPECNAMELEN */
+#endif
+
 #define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
 
 /* Not all systems have MAP_FAILED defined */
@@ -1030,7 +1034,7 @@
 
 static int drmGetMinorType(int major, int minor)
 {
-#ifdef __FreeBSD__
+#if defined(__FreeBSD__) || defined(__DragonFly__)
     char name[SPECNAMELEN];
     int id;
 
@@ -3330,6 +3334,8 @@
     snprintf(path, sizeof(path), "/sys/dev/char/%d:%d/device/drm",
              maj, min);
     return stat(path, &sbuf) == 0;
+#elif defined(__DragonFly__)
+    return true;	/* DragonFly BSD has no fixed major device numbers */
 #elif defined(__FreeBSD__)
     char name[SPECNAMELEN];
 
@@ -3453,7 +3459,7 @@
 
     closedir(sysdir);
     return NULL;
-#elif defined(__FreeBSD__)
+#elif defined(__FreeBSD__) || defined(__DragonFly__)
     struct stat sbuf;
     char dname[SPECNAMELEN];
     const char *mname;
