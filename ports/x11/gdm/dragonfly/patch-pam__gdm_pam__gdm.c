--- pam_gdm/pam_gdm.c.orig
+++ pam_gdm/pam_gdm.c
@@ -21,11 +21,13 @@
 
 #include <unistd.h>
 
+#include <security/pam_modules.h>
+#ifdef __linux__
 #include <security/_pam_macros.h>
 #include <security/pam_ext.h>
 #include <security/pam_misc.h>
-#include <security/pam_modules.h>
 #include <security/pam_modutil.h>
+#endif
 
 #ifdef HAVE_KEYUTILS
 #include <keyutils.h>
