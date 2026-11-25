--- src/quirks.c.orig
+++ src/quirks.c
@@ -38,3 +38,3 @@
-#ifdef __FreeBSD__
+#if defined(__FreeBSD__) || defined(__DragonFly__)
 #include <kenv.h>
 #endif
@@ -451,7 +451,7 @@
 }
 #endif
 
-#ifdef __FreeBSD__
+#if defined(__FreeBSD__) || defined(__DragonFly__)
 static inline char *
 init_dmi_freebsd(void)
 {
@@ -462,6 +462,14 @@
 	char board_vendor[LEN], board_name[LEN], board_version[LEN];
 	char chassis_vendor[LEN], chassis_type[LEN], chassis_version[LEN];
 	int chassis_type_num = 0x2;
+
+#if defined(__DragonFly__)
+	/* kenv() leaves the buffer alone when a name is not set */
+	bios_vendor[0] = bios_version[0] = bios_date[0] = '\0';
+	sys_vendor[0] = product_name[0] = product_version[0] = '\0';
+	board_vendor[0] = board_name[0] = board_version[0] = '\0';
+	chassis_vendor[0] = chassis_type[0] = chassis_version[0] = '\0';
+#endif
 
 	kenv(KENV_GET, "smbios.bios.vendor", bios_vendor, LEN);
 	kenv(KENV_GET, "smbios.bios.version", bios_version, LEN);
@@ -472,7 +480,11 @@
 	kenv(KENV_GET, "smbios.planar.maker", board_vendor, LEN);
 	kenv(KENV_GET, "smbios.planar.product", board_name, LEN);
 	kenv(KENV_GET, "smbios.planar.version", board_version, LEN);
+#if defined(__DragonFly__)
+	kenv(KENV_GET, "smbios.chassis.maker", chassis_vendor, LEN);
+#else
 	kenv(KENV_GET, "smbios.chassis.vendor", chassis_vendor, LEN);
+#endif
 	kenv(KENV_GET, "smbios.chassis.type", chassis_type, LEN);
 	kenv(KENV_GET, "smbios.chassis.version", chassis_version, LEN);
 #undef LEN
@@ -520,7 +532,7 @@
 
 #if defined(__linux__)
 	return init_dmi_linux();
-#elif defined(__FreeBSD__)
+#elif defined(__FreeBSD__) || defined(__DragonFly__)
 	return init_dmi_freebsd();
 #else
 	return NULL;
