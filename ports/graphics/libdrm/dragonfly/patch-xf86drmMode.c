--- xf86drmMode.c.orig	2014-05-02 15:05:03.000000000 +0000
+++ xf86drmMode.c
@@ -806,6 +806,8 @@ int drmCheckModesettingSupported(const c
 			return -EINVAL;
 		return (modesetting ? 0 : -ENOSYS);
 	}
+#elif defined(__DragonFly__)
+	return 0;
 #endif
 	return -ENOSYS;
 
